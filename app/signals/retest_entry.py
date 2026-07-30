"""Retest / pullback entry thesis (canonical arm B).

Arm at signal time; fill only if price revisits the ATR pullback zone
(0.35–1.0 × ATR from the signal reference entry) before pending expiry
(``pending_multiplier × primary_timeframe``). Skip if the original stop is
touched first or the window expires without a fill. The fill uses the least
favourable price the candle actually traded inside the zone, not the midpoint.

Shared by live paper trading, backtests, and counterfactual scripts.
Matches ``scripts/simulate_paper_variants.py`` / ``scripts/backtest_retest_variants.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.core.enums import SignalDirection
from app.core.time import ensure_utc, timeframe_to_timedelta
from app.market_data.types import Candle
from app.signals.risk import DEFAULT_TP_MULTIPLIERS

ZONE_NEAR = Decimal("0.35")
ZONE_FAR = Decimal("1.0")
ATR_PERIOD = 14
DEFAULT_PENDING_MULTIPLIER = 4


@dataclass(frozen=True)
class RetestEntryConfig:
    zone_near: Decimal = ZONE_NEAR
    zone_far: Decimal = ZONE_FAR
    atr_period: int = ATR_PERIOD
    pending_multiplier: int = DEFAULT_PENDING_MULTIPLIER


@dataclass
class RetestArmResult:
    status: str
    fill_price: float | None = None
    fill_time: datetime | None = None
    stop: float | None = None
    zone_lo: float | None = None
    zone_hi: float | None = None
    atr: float | None = None
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


def retest_zone(
    reference: Decimal,
    atr: Decimal,
    *,
    is_long: bool,
    zone_near: Decimal = ZONE_NEAR,
    zone_far: Decimal = ZONE_FAR,
) -> tuple[Decimal, Decimal]:
    near = atr * zone_near
    far = atr * zone_far
    if is_long:
        return reference - far, reference - near
    return reference + near, reference + far


def zone_fill_price(
    *,
    low: Decimal,
    high: Decimal,
    zone_lo: Decimal,
    zone_hi: Decimal,
    is_long: bool,
) -> Decimal:
    """Unguenstigster Preis, den die Kerze innerhalb der Zone gehandelt hat.

    Der Zonen-Mittelpunkt ist nur dann erreichbar, wenn die Kerze auch wirklich
    so tief in die Zone laeuft. Ein Fill am Mittelpunkt schenkt sonst bis zu
    ``(zone_far - zone_near) / 2`` ATR pro Trade — und weil der Stop am Fill
    haengt, verschiebt das den kompletten Trade und macht jeden Replay zu
    optimistisch. Fuer Longs zaehlt daher der hoechste, fuer Shorts der
    niedrigste in der Zone gehandelte Preis.
    """
    return min(high, zone_hi) if is_long else max(low, zone_lo)


def levels_from_entry_sl(
    entry: Decimal,
    stop: Decimal,
    *,
    is_long: bool,
    multipliers: tuple[Decimal, ...] | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    mults = multipliers or tuple(Decimal(str(m)) for m in DEFAULT_TP_MULTIPLIERS)
    risk = abs(entry - stop)
    if risk <= 0:
        risk = entry * Decimal("0.01")
    direction = Decimal("1") if is_long else Decimal("-1")
    tps = tuple(entry + direction * m * risk for m in mults)
    return tps[0], tps[1], tps[2]


def stop_from_retest_fill(
    fill: Decimal,
    *,
    reference_entry: Decimal,
    original_stop: Decimal,
    is_long: bool,
) -> Decimal:
    """Keep original R distance, anchored at the retest fill (winning arm B)."""
    old_risk = abs(reference_entry - original_stop)
    if old_risk <= 0:
        old_risk = fill * Decimal("0.01")
    return fill - old_risk if is_long else fill + old_risk


def arm_retest_entry(
    *,
    direction: str | SignalDirection,
    arm_time: datetime,
    reference_entry: float,
    original_stop: float,
    timeframe: str,
    candles: list[Candle],
    config: RetestEntryConfig | None = None,
) -> RetestArmResult:
    """Wait for ATR pullback-zone touch; keep original R at fill.

    Status values:
      - filled
      - skipped_no_history
      - skipped_no_atr
      - skipped_expiry
      - skipped_sl
      - pending (data ended before fill, pending window still open)
    """
    cfg = config or RetestEntryConfig()
    direction_enum = SignalDirection(direction) if isinstance(direction, str) else direction
    is_long = direction_enum.is_long
    arm_time = ensure_utc(arm_time)
    pending_until = arm_time + cfg.pending_multiplier * timeframe_to_timedelta(timeframe)
    reference = Decimal(str(reference_entry))
    orig_sl = Decimal(str(original_stop))

    sig_idx = idx_at_or_before(candles, arm_time)
    if sig_idx is None:
        return RetestArmResult(status="skipped_no_history", note="no_bar_at_signal")

    atr_f = wilder_atr(candles, sig_idx, period=cfg.atr_period)
    if atr_f is None:
        return RetestArmResult(status="skipped_no_atr", note="insufficient_atr_history")
    atr = Decimal(str(atr_f))
    zone_lo, zone_hi = retest_zone(
        reference,
        atr,
        is_long=is_long,
        zone_near=cfg.zone_near,
        zone_far=cfg.zone_far,
    )

    bars_waited = 0
    now_cap = ensure_utc(candles[-1].open_time) if candles else arm_time

    for candle in candles[sig_idx + 1 :]:
        when = ensure_utc(candle.open_time)
        if when > pending_until:
            return RetestArmResult(
                status="skipped_expiry",
                zone_lo=float(zone_lo),
                zone_hi=float(zone_hi),
                atr=float(atr),
                bars_waited=bars_waited,
                note="pending_expired",
            )
        bars_waited += 1
        high = Decimal(str(float(candle.high)))
        low = Decimal(str(float(candle.low)))

        if is_long and low <= orig_sl:
            return RetestArmResult(
                status="skipped_sl",
                zone_lo=float(zone_lo),
                zone_hi=float(zone_hi),
                atr=float(atr),
                bars_waited=bars_waited,
                note="sl_before_retest",
            )
        if (not is_long) and high >= orig_sl:
            return RetestArmResult(
                status="skipped_sl",
                zone_lo=float(zone_lo),
                zone_hi=float(zone_hi),
                atr=float(atr),
                bars_waited=bars_waited,
                note="sl_before_retest",
            )

        if low <= zone_hi and high >= zone_lo:
            fill = zone_fill_price(
                low=low,
                high=high,
                zone_lo=zone_lo,
                zone_hi=zone_hi,
                is_long=is_long,
            )
            stop = stop_from_retest_fill(
                fill,
                reference_entry=reference,
                original_stop=orig_sl,
                is_long=is_long,
            )
            return RetestArmResult(
                status="filled",
                fill_price=float(fill),
                fill_time=when,
                stop=float(stop),
                zone_lo=float(zone_lo),
                zone_hi=float(zone_hi),
                atr=float(atr),
                bars_waited=bars_waited,
                note="retest_zone_fill",
            )

    if now_cap < pending_until:
        return RetestArmResult(
            status="pending",
            zone_lo=float(zone_lo),
            zone_hi=float(zone_hi),
            atr=float(atr),
            bars_waited=bars_waited,
            note="awaiting_retest",
        )

    return RetestArmResult(
        status="skipped_expiry",
        zone_lo=float(zone_lo),
        zone_hi=float(zone_hi),
        atr=float(atr),
        bars_waited=bars_waited,
        note="data_ended_before_fill",
    )
