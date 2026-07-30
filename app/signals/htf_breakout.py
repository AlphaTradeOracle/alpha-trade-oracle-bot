"""HTF breakout confirmation thesis (canonical rules).

Long only after a confirmed 4h candle *close* above resistance (lookback high);
Short only after confirmed 4h close below support (lookback low). Stop is placed
near structure (recent swing), not a chase fill inside the level.

Shared by live paper trading, backtests, and historical verification scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.core.enums import SignalDirection
from app.core.time import ensure_utc, timeframe_to_timedelta
from app.market_data.types import Candle

DEFAULT_CONFIRM_TF = "4h"
DEFAULT_LOOKBACK_BARS = 180  # ~30 days of 4h bars
DEFAULT_PENDING_DAYS = 14
ATR_PERIOD = 14


@dataclass(frozen=True)
class HtfBreakoutConfig:
    confirm_timeframe: str = DEFAULT_CONFIRM_TF
    lookback_bars: int = DEFAULT_LOOKBACK_BARS
    pending_days: int = DEFAULT_PENDING_DAYS


@dataclass
class HtfArmResult:
    status: str
    fill_price: float | None = None
    fill_time: datetime | None = None
    level: float | None = None
    stop: float | None = None
    bars_waited: int = 0
    note: str = ""

    @property
    def filled(self) -> bool:
        return self.status == "filled"


def wilder_atr(candles: list[Candle], end_idx: int, period: int = ATR_PERIOD) -> float | None:
    if end_idx < period:
        return None
    trs: list[float] = []
    for i in range(1, end_idx + 1):
        high = float(candles[i].high)
        low = float(candles[i].low)
        prev_c = float(candles[i - 1].close)
        trs.append(max(high - low, abs(high - prev_c), abs(low - prev_c)))
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr if atr > 0 else None


def idx_at_or_before(candles: list[Candle], when: datetime) -> int | None:
    when = ensure_utc(when)
    idx = None
    for i, candle in enumerate(candles):
        if ensure_utc(candle.open_time) <= when:
            idx = i
        else:
            break
    return idx


def levels_from_entry_sl(
    entry: Decimal,
    stop: Decimal,
    *,
    is_long: bool,
    multipliers: tuple[Decimal, Decimal, Decimal] = (
        Decimal("2"),
        Decimal("4"),
        Decimal("6"),
    ),
) -> tuple[Decimal, Decimal, Decimal]:
    risk = abs(entry - stop)
    if risk <= 0:
        risk = entry * Decimal("0.01")
    direction = Decimal("1") if is_long else Decimal("-1")
    tps = tuple(entry + direction * m * risk for m in multipliers)
    return tps[0], tps[1], tps[2]


def arm_htf_breakout(
    *,
    direction: str | SignalDirection,
    arm_time: datetime,
    original_stop: float,
    candles_4h: list[Candle],
    config: HtfBreakoutConfig | None = None,
) -> HtfArmResult:
    """Wait for confirmed 4h close beyond lookback high/low; structure SL.

    Status values:
      - filled
      - skipped_no_history
      - skipped_expiry
      - skipped_sl
      - pending (data ended before confirm, but pending window still open)
    """
    cfg = config or HtfBreakoutConfig()
    direction_enum = SignalDirection(direction) if isinstance(direction, str) else direction
    is_long = direction_enum.is_long
    arm_time = ensure_utc(arm_time)
    pending_until = arm_time + timedelta(days=cfg.pending_days)
    confirm_delta = timeframe_to_timedelta(cfg.confirm_timeframe)

    sig_idx = idx_at_or_before(candles_4h, arm_time)
    if sig_idx is None or sig_idx < 5:
        return HtfArmResult(status="skipped_no_history", note="insufficient_4h_history")

    start = max(0, sig_idx - cfg.lookback_bars)
    window = candles_4h[start:sig_idx]  # exclude signal bar
    if len(window) < 10:
        return HtfArmResult(status="skipped_no_history", note="lookback_too_short")

    level = max(float(c.high) for c in window) if is_long else min(float(c.low) for c in window)
    orig_sl = Decimal(str(original_stop))
    bars_waited = 0
    now_cap = ensure_utc(candles_4h[-1].close_time) if candles_4h[-1].close_time else (
        ensure_utc(candles_4h[-1].open_time) + confirm_delta
    )

    for i in range(sig_idx + 1, len(candles_4h)):
        candle = candles_4h[i]
        when = ensure_utc(candle.open_time)
        close_when = (
            ensure_utc(candle.close_time)
            if candle.close_time
            else when + confirm_delta
        )
        if close_when > pending_until:
            return HtfArmResult(
                status="skipped_expiry",
                level=level,
                bars_waited=bars_waited,
                note="no_4h_close_beyond_level",
            )
        bars_waited += 1
        high = Decimal(str(float(candle.high)))
        low = Decimal(str(float(candle.low)))
        close = Decimal(str(float(candle.close)))

        if is_long and low <= orig_sl:
            return HtfArmResult(
                status="skipped_sl",
                level=level,
                bars_waited=bars_waited,
                note="sl_before_breakout_confirm",
            )
        if (not is_long) and high >= orig_sl:
            return HtfArmResult(
                status="skipped_sl",
                level=level,
                bars_waited=bars_waited,
                note="sl_before_breakdown_confirm",
            )

        confirmed = close > Decimal(str(level)) if is_long else close < Decimal(str(level))
        if not confirmed:
            continue

        fill = close
        pre = candles_4h[max(0, i - 8) : i] or window[-8:]
        if is_long:
            struct = Decimal(str(min(float(x.low) for x in pre)))
            fail_break = Decimal(str(level)) * Decimal("0.995")
            stop = max(struct, fail_break) if struct < fill else fail_break
            if stop >= fill:
                atr = wilder_atr(candles_4h, i)
                stop = fill - Decimal(str(atr or float(fill) * 0.02)) * Decimal("1.5")
        else:
            struct = Decimal(str(max(float(x.high) for x in pre)))
            fail_break = Decimal(str(level)) * Decimal("1.005")
            stop = min(struct, fail_break) if struct > fill else fail_break
            if stop <= fill:
                atr = wilder_atr(candles_4h, i)
                stop = fill + Decimal(str(atr or float(fill) * 0.02)) * Decimal("1.5")

        return HtfArmResult(
            status="filled",
            fill_price=float(fill),
            fill_time=close_when,
            level=level,
            stop=float(stop),
            bars_waited=bars_waited,
            note="4h_close_beyond_level",
        )

    if now_cap < pending_until:
        return HtfArmResult(
            status="pending",
            level=level,
            bars_waited=bars_waited,
            note="awaiting_4h_close_beyond_level",
        )

    return HtfArmResult(
        status="skipped_expiry",
        level=level,
        bars_waited=bars_waited,
        note="data_ended_before_confirm",
    )
