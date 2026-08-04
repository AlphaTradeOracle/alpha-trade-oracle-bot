"""Retest / pullback entry thesis (canonical arm B).

Arm at signal time; fill only if price revisits the ATR pullback zone
(0.40–1.15 × ATR from the signal reference entry by default) before pending expiry
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
from app.indicators.trendlines import (
    TrendlineDetectConfig,
    TrendlineGateConfig,
    evaluate_retest_trendline_gate,
)
from app.market_data.types import Candle
from app.signals.risk import DEFAULT_TP_MULTIPLIERS

ZONE_NEAR = Decimal("0.40")
ZONE_FAR = Decimal("1.15")
ATR_PERIOD = 14
DEFAULT_PENDING_MULTIPLIER = 6
DEFAULT_MIN_BARS_IN_ZONE = 1
DEFAULT_TRENDLINE_BUFFER_ATR = 0.1
DEFAULT_TRENDLINE_LOOKBACK = 40
DEFAULT_TRENDLINE_MIN_POINTS = 2
DEFAULT_TRENDLINE_MIN_R2 = 0.85


@dataclass(frozen=True)
class RetestEntryConfig:
    zone_near: Decimal = ZONE_NEAR
    zone_far: Decimal = ZONE_FAR
    atr_period: int = ATR_PERIOD
    pending_multiplier: int = DEFAULT_PENDING_MULTIPLIER
    min_bars_in_zone: int = DEFAULT_MIN_BARS_IN_ZONE
    #: Retest: Fill verwerfen wenn Bounce die Diagonale bricht
    #: (Short: fallender Widerstand / Long: aufsteigender Support).
    trendline_gate_enabled: bool = True
    trendline_buffer_atr: float = DEFAULT_TRENDLINE_BUFFER_ATR
    trendline_lookback: int = DEFAULT_TRENDLINE_LOOKBACK
    trendline_min_points: int = DEFAULT_TRENDLINE_MIN_POINTS
    trendline_min_r2: float = DEFAULT_TRENDLINE_MIN_R2
    trendline_min_clearance_atr: float = 0.0
    #: Deprecated alias — maps to ``trendline_buffer_atr`` when set via older call sites.
    trendline_tol_atr: float | None = None


@dataclass
class RetestArmResult:
    status: str
    fill_price: float | None = None
    fill_time: datetime | None = None
    # Event time for terminal skips (used as closed_at so rebuild/live busy
    # checks free the symbol at the real skip bar, not wall-clock "now").
    resolved_at: datetime | None = None
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


def zone_overlaps_stop(
    zone_lo: Decimal,
    zone_hi: Decimal,
    stop: Decimal,
) -> bool:
    """True when the original stop sits inside the retest zone (inclusive)."""
    lo = min(zone_lo, zone_hi)
    hi = max(zone_lo, zone_hi)
    return lo <= stop <= hi


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
      - skipped_trendline_break (broke / too close to falling resistance or rising support)
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
        return RetestArmResult(
            status="skipped_no_history",
            resolved_at=arm_time,
            note="no_bar_at_signal",
        )

    atr_f = wilder_atr(candles, sig_idx, period=cfg.atr_period)
    if atr_f is None:
        return RetestArmResult(
            status="skipped_no_atr",
            resolved_at=arm_time,
            note="insufficient_atr_history",
        )
    atr = Decimal(str(atr_f))
    zone_lo, zone_hi = retest_zone(
        reference,
        atr,
        is_long=is_long,
        zone_near=cfg.zone_near,
        zone_far=cfg.zone_far,
    )
    # Same gate as live arm: SL inside the retest zone makes R undefined /
    # toxic. Must live here (not only in _open_pending_retest) because rebuild
    # from stored signals has empty assessments → no ATR at pending-create time.
    if zone_overlaps_stop(zone_lo, zone_hi, orig_sl):
        return RetestArmResult(
            status="skipped_zone_stop_overlap",
            resolved_at=arm_time,
            zone_lo=float(zone_lo),
            zone_hi=float(zone_hi),
            atr=float(atr),
            note="stop_inside_retest_zone",
        )

    bars_waited = 0
    bars_in_zone = 0
    now_cap = ensure_utc(candles[-1].open_time) if candles else arm_time

    for fill_idx, candle in enumerate(candles[sig_idx + 1 :], start=sig_idx + 1):
        when = ensure_utc(candle.open_time)
        if when > pending_until:
            return RetestArmResult(
                status="skipped_expiry",
                resolved_at=pending_until,
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
                resolved_at=when,
                zone_lo=float(zone_lo),
                zone_hi=float(zone_hi),
                atr=float(atr),
                bars_waited=bars_waited,
                note="sl_before_retest",
            )
        if (not is_long) and high >= orig_sl:
            return RetestArmResult(
                status="skipped_sl",
                resolved_at=when,
                zone_lo=float(zone_lo),
                zone_hi=float(zone_hi),
                atr=float(atr),
                bars_waited=bars_waited,
                note="sl_before_retest",
            )

        if low <= zone_hi and high >= zone_lo:
            bars_in_zone += 1
            if bars_in_zone >= max(1, cfg.min_bars_in_zone):
                fill = zone_fill_price(
                    low=low,
                    high=high,
                    zone_lo=zone_lo,
                    zone_hi=zone_hi,
                    is_long=is_long,
                )
                if cfg.trendline_gate_enabled and atr_f > 0:
                    buffer = (
                        float(cfg.trendline_tol_atr)
                        if cfg.trendline_tol_atr is not None
                        else float(cfg.trendline_buffer_atr)
                    )
                    gate = evaluate_retest_trendline_gate(
                        candles,
                        fill_idx=fill_idx,
                        fill_price=float(fill),
                        atr=float(atr_f),
                        is_long=is_long,
                        cfg=TrendlineGateConfig(
                            enabled=True,
                            buffer_atr=buffer,
                            min_clearance_atr=float(cfg.trendline_min_clearance_atr),
                            detect=TrendlineDetectConfig(
                                lookback=max(10, int(cfg.trendline_lookback)),
                                min_points=max(2, int(cfg.trendline_min_points)),
                                min_r2=float(cfg.trendline_min_r2),
                            ),
                        ),
                    )
                    if gate.blocked:
                        return RetestArmResult(
                            status="skipped_trendline_break",
                            resolved_at=when,
                            zone_lo=float(zone_lo),
                            zone_hi=float(zone_hi),
                            atr=float(atr),
                            bars_waited=bars_waited,
                            note=(
                                f"{gate.reason}"
                                + (
                                    f":line={gate.line_price:.8g}"
                                    if gate.line_price is not None
                                    else ""
                                )
                                + f";fill={float(fill):.8g}"
                            ),
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
                    resolved_at=when,
                    stop=float(stop),
                    zone_lo=float(zone_lo),
                    zone_hi=float(zone_hi),
                    atr=float(atr),
                    bars_waited=bars_waited,
                    note="retest_zone_fill",
                )
        else:
            bars_in_zone = 0

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
        resolved_at=pending_until,
        zone_lo=float(zone_lo),
        zone_hi=float(zone_hi),
        atr=float(atr),
        bars_waited=bars_waited,
        note="data_ended_before_fill",
    )
