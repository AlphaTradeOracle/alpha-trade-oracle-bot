"""Diagonale Trendlinien aus Swing-Punkten (kein Look-ahead).

Fallender Widerstand (Lower Highs) und steigender Support (Higher Lows)
auf dem Primary-TF. Linie muss frisch sein (Lookback), R²/Steigung ok.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from app.indicators.structure import SwingPoint, find_swing_points
from app.market_data.types import Candle

Reason = Literal[
    "",
    "broke_falling_resistance",
    "broke_rising_support",
    "too_close_falling_resistance",
    "too_close_rising_support",
]


@dataclass(frozen=True)
class Trendline:
    """``price = slope * bar_index + intercept`` (Index in der Candle-Serie)."""

    kind: Literal["falling_resistance", "rising_support"]
    slope: float
    intercept: float
    pivot_indices: tuple[int, ...]
    pivot_prices: tuple[float, ...]
    r2: float
    age_bars: int

    def price_at(self, bar_index: int) -> float:
        return float(self.slope * bar_index + self.intercept)

    @property
    def is_descending(self) -> bool:
        return self.slope < 0

    @property
    def is_ascending(self) -> bool:
        return self.slope > 0


@dataclass(frozen=True)
class TrendlineGateResult:
    blocked: bool
    reason: Reason
    line_price: float | None = None
    fill_price: float | None = None
    line: Trendline | None = None

    @property
    def desk_note(self) -> str:
        if not self.blocked or not self.reason:
            return ""
        return f"blocked: {self.reason.replace('_', ' ')}"


@dataclass(frozen=True)
class TrendlineDetectConfig:
    lookback: int = 40
    min_points: int = 2
    min_r2: float = 0.85
    #: Verwerfe Linien deren |ΔPreis| ueber Lookback > max_slope_atr × ATR.
    max_slope_atr: float = 8.0
    swing_left: int = 3
    swing_right: int = 3


@dataclass(frozen=True)
class TrendlineGateConfig:
    enabled: bool = True
    buffer_atr: float = 0.1
    #: 0 = aus. Short: Fill muss mind. so weit unter der Linie liegen.
    min_clearance_atr: float = 0.0
    detect: TrendlineDetectConfig = TrendlineDetectConfig()


def _fit_line(chain: list[SwingPoint]) -> tuple[float, float, float] | None:
    """Return slope, intercept, r²."""
    if len(chain) < 2:
        return None
    xs = np.array([float(p.index) for p in chain], dtype=float)
    ys = np.array([float(p.price) for p in chain], dtype=float)
    if xs[-1] == xs[0]:
        return None
    if len(xs) == 2:
        slope = float((ys[1] - ys[0]) / (xs[1] - xs[0]))
        intercept = float(ys[0] - slope * xs[0])
        return slope, intercept, 1.0
    slope, intercept = (float(v) for v in np.polyfit(xs, ys, 1))
    pred = slope * xs + intercept
    ss_res = float(np.sum((ys - pred) ** 2))
    ss_tot = float(np.sum((ys - float(ys.mean())) ** 2))
    r2 = 1.0 if ss_tot <= 1e-18 else max(0.0, 1.0 - ss_res / ss_tot)
    return slope, intercept, r2


def _descending_swing_highs(
    swings: list[SwingPoint], *, min_points: int
) -> list[SwingPoint]:
    highs = [s for s in swings if s.is_high]
    if len(highs) < min_points:
        return []
    chain = [highs[-1]]
    for prev in reversed(highs[:-1]):
        if prev.price > chain[-1].price:
            chain.append(prev)
        else:
            break
    chain.reverse()
    return chain if len(chain) >= min_points else []


def _ascending_swing_lows(
    swings: list[SwingPoint], *, min_points: int
) -> list[SwingPoint]:
    lows = [s for s in swings if not s.is_high]
    if len(lows) < min_points:
        return []
    chain = [lows[-1]]
    for prev in reversed(lows[:-1]):
        if prev.price < chain[-1].price:
            chain.append(prev)
        else:
            break
    chain.reverse()
    return chain if len(chain) >= min_points else []


def _slope_ok(
    slope: float,
    *,
    lookback: int,
    atr: float | None,
    max_slope_atr: float,
    descending: bool,
) -> bool:
    if descending and slope >= 0:
        return False
    if (not descending) and slope <= 0:
        return False
    if atr is None or atr <= 0 or lookback <= 0:
        return True
    span = abs(slope) * float(lookback)
    return span <= float(max_slope_atr) * float(atr)


def fit_falling_resistance(
    swings: list[SwingPoint],
    *,
    eval_idx: int,
    atr: float | None = None,
    cfg: TrendlineDetectConfig | None = None,
) -> Trendline | None:
    cfg = cfg or TrendlineDetectConfig()
    chain = _descending_swing_highs(swings, min_points=cfg.min_points)
    fitted = _fit_line(chain)
    if fitted is None:
        return None
    slope, intercept, r2 = fitted
    if r2 < cfg.min_r2:
        return None
    if not _slope_ok(
        slope,
        lookback=cfg.lookback,
        atr=atr,
        max_slope_atr=cfg.max_slope_atr,
        descending=True,
    ):
        return None
    age = max(0, eval_idx - chain[-1].index)
    if age > cfg.lookback:
        return None
    return Trendline(
        kind="falling_resistance",
        slope=slope,
        intercept=intercept,
        pivot_indices=tuple(p.index for p in chain),
        pivot_prices=tuple(p.price for p in chain),
        r2=r2,
        age_bars=age,
    )


def fit_rising_support(
    swings: list[SwingPoint],
    *,
    eval_idx: int,
    atr: float | None = None,
    cfg: TrendlineDetectConfig | None = None,
) -> Trendline | None:
    cfg = cfg or TrendlineDetectConfig()
    chain = _ascending_swing_lows(swings, min_points=cfg.min_points)
    fitted = _fit_line(chain)
    if fitted is None:
        return None
    slope, intercept, r2 = fitted
    if r2 < cfg.min_r2:
        return None
    if not _slope_ok(
        slope,
        lookback=cfg.lookback,
        atr=atr,
        max_slope_atr=cfg.max_slope_atr,
        descending=False,
    ):
        return None
    age = max(0, eval_idx - chain[-1].index)
    if age > cfg.lookback:
        return None
    return Trendline(
        kind="rising_support",
        slope=slope,
        intercept=intercept,
        pivot_indices=tuple(p.index for p in chain),
        pivot_prices=tuple(p.price for p in chain),
        r2=r2,
        age_bars=age,
    )


# Back-compat aliases used by older tests / imports.
def fit_descending_resistance(
    swings: list[SwingPoint], *, min_points: int = 2
) -> Trendline | None:
    eval_idx = max((s.index for s in swings), default=0)
    return fit_falling_resistance(
        swings,
        eval_idx=eval_idx,
        cfg=TrendlineDetectConfig(min_points=min_points, min_r2=0.0, max_slope_atr=1e9),
    )


def fit_ascending_support(
    swings: list[SwingPoint], *, min_points: int = 2
) -> Trendline | None:
    eval_idx = max((s.index for s in swings), default=0)
    return fit_rising_support(
        swings,
        eval_idx=eval_idx,
        cfg=TrendlineDetectConfig(min_points=min_points, min_r2=0.0, max_slope_atr=1e9),
    )


def swings_from_candles(
    candles: list[Candle],
    *,
    end_idx: int,
    lookback: int = 40,
    left: int = 3,
    right: int = 3,
) -> list[SwingPoint]:
    """Swing points on the lookback window ending at ``end_idx`` (no future bars)."""
    if end_idx < left + right + 2:
        return []
    start = max(0, end_idx - max(1, lookback) + 1)
    window = candles[start : end_idx + 1]
    if len(window) < left + right + 2:
        return []
    high = pd.Series([float(c.high) for c in window], dtype=float)
    low = pd.Series([float(c.low) for c in window], dtype=float)
    local = find_swing_points(high, low, left=left, right=right)
    return [
        SwingPoint(index=start + s.index, price=s.price, is_high=s.is_high)
        for s in local
    ]


def detect_trendlines(
    candles: list[Candle],
    *,
    end_idx: int,
    atr: float | None = None,
    cfg: TrendlineDetectConfig | None = None,
) -> tuple[Trendline | None, Trendline | None]:
    """Return (falling_resistance, rising_support) at ``end_idx``."""
    cfg = cfg or TrendlineDetectConfig()
    if end_idx <= 0 or end_idx >= len(candles):
        return None, None
    swings = swings_from_candles(
        candles,
        end_idx=end_idx,
        lookback=cfg.lookback,
        left=cfg.swing_left,
        right=cfg.swing_right,
    )
    falling = fit_falling_resistance(swings, eval_idx=end_idx, atr=atr, cfg=cfg)
    rising = fit_rising_support(swings, eval_idx=end_idx, atr=atr, cfg=cfg)
    return falling, rising


def line_prices_at(
    candles: list[Candle],
    *,
    end_idx: int,
    atr: float | None = None,
    cfg: TrendlineDetectConfig | None = None,
) -> tuple[float | None, float | None]:
    """Current diagonal prices for structure snapshots."""
    falling, rising = detect_trendlines(candles, end_idx=end_idx, atr=atr, cfg=cfg)
    fall_px = falling.price_at(end_idx) if falling is not None else None
    rise_px = rising.price_at(end_idx) if rising is not None else None
    return fall_px, rise_px


def _short_vs_line(
    *,
    fill_price: float,
    high: float,
    line_price: float,
    atr: float,
    buffer_atr: float,
    min_clearance_atr: float,
    line: Trendline,
) -> TrendlineGateResult:
    buf = float(buffer_atr) * float(atr)
    # Durchbruch nach oben (Docht oder Fill).
    if high > line_price + buf or fill_price > line_price + buf:
        return TrendlineGateResult(
            blocked=True,
            reason="broke_falling_resistance",
            line_price=line_price,
            fill_price=fill_price,
            line=line,
        )
    # Optional: zu nah unter der Dachlinie.
    if min_clearance_atr > 0:
        clearance = line_price - fill_price
        need = float(min_clearance_atr) * float(atr)
        if 0.0 <= clearance < need:
            return TrendlineGateResult(
                blocked=True,
                reason="too_close_falling_resistance",
                line_price=line_price,
                fill_price=fill_price,
                line=line,
            )
    return TrendlineGateResult(
        blocked=False,
        reason="",
        line_price=line_price,
        fill_price=fill_price,
        line=line,
    )


def _long_vs_line(
    *,
    fill_price: float,
    low: float,
    line_price: float,
    atr: float,
    buffer_atr: float,
    min_clearance_atr: float,
    line: Trendline,
) -> TrendlineGateResult:
    buf = float(buffer_atr) * float(atr)
    if low < line_price - buf or fill_price < line_price - buf:
        return TrendlineGateResult(
            blocked=True,
            reason="broke_rising_support",
            line_price=line_price,
            fill_price=fill_price,
            line=line,
        )
    if min_clearance_atr > 0:
        clearance = fill_price - line_price
        need = float(min_clearance_atr) * float(atr)
        if 0.0 <= clearance < need:
            return TrendlineGateResult(
                blocked=True,
                reason="too_close_rising_support",
                line_price=line_price,
                fill_price=fill_price,
                line=line,
            )
    return TrendlineGateResult(
        blocked=False,
        reason="",
        line_price=line_price,
        fill_price=fill_price,
        line=line,
    )


def evaluate_retest_trendline_gate(
    candles: list[Candle],
    *,
    fill_idx: int,
    fill_price: float,
    atr: float,
    is_long: bool,
    cfg: TrendlineGateConfig | None = None,
) -> TrendlineGateResult:
    """Gate at retest fill: block if price breaks / sits too close to the diagonal."""
    cfg = cfg or TrendlineGateConfig()
    if not cfg.enabled or atr <= 0 or fill_idx <= 0 or fill_idx >= len(candles):
        return TrendlineGateResult(blocked=False, reason="", fill_price=fill_price)

    falling, rising = detect_trendlines(
        candles, end_idx=fill_idx, atr=atr, cfg=cfg.detect
    )
    candle = candles[fill_idx]
    if is_long:
        if rising is None:
            return TrendlineGateResult(blocked=False, reason="", fill_price=fill_price)
        line_price = rising.price_at(fill_idx)
        return _long_vs_line(
            fill_price=float(fill_price),
            low=float(candle.low),
            line_price=line_price,
            atr=float(atr),
            buffer_atr=cfg.buffer_atr,
            min_clearance_atr=cfg.min_clearance_atr,
            line=rising,
        )

    if falling is None:
        return TrendlineGateResult(blocked=False, reason="", fill_price=fill_price)
    line_price = falling.price_at(fill_idx)
    return _short_vs_line(
        fill_price=float(fill_price),
        high=float(candle.high),
        line_price=line_price,
        atr=float(atr),
        buffer_atr=cfg.buffer_atr,
        min_clearance_atr=cfg.min_clearance_atr,
        line=falling,
    )


# Legacy helpers (wick-only) kept for older call sites / tests.
def short_fill_breaks_descending_resistance(
    candles: list[Candle],
    *,
    fill_idx: int,
    atr: float,
    min_points: int = 2,
    tol_atr: float = 0.1,
    swing_left: int = 3,
    swing_right: int = 3,
) -> tuple[bool, Trendline | None, float | None]:
    cfg = TrendlineGateConfig(
        enabled=True,
        buffer_atr=tol_atr,
        detect=TrendlineDetectConfig(
            min_points=min_points,
            swing_left=swing_left,
            swing_right=swing_right,
            min_r2=0.0,
            max_slope_atr=1e9,
        ),
    )
    fill_price = float(candles[fill_idx].high)
    result = evaluate_retest_trendline_gate(
        candles,
        fill_idx=fill_idx,
        fill_price=fill_price,
        atr=atr,
        is_long=False,
        cfg=cfg,
    )
    return result.blocked, result.line, result.line_price


def long_fill_breaks_ascending_support(
    candles: list[Candle],
    *,
    fill_idx: int,
    atr: float,
    min_points: int = 2,
    tol_atr: float = 0.1,
    swing_left: int = 3,
    swing_right: int = 3,
) -> tuple[bool, Trendline | None, float | None]:
    cfg = TrendlineGateConfig(
        enabled=True,
        buffer_atr=tol_atr,
        detect=TrendlineDetectConfig(
            min_points=min_points,
            swing_left=swing_left,
            swing_right=swing_right,
            min_r2=0.0,
            max_slope_atr=1e9,
        ),
    )
    fill_price = float(candles[fill_idx].low)
    result = evaluate_retest_trendline_gate(
        candles,
        fill_idx=fill_idx,
        fill_price=fill_price,
        atr=atr,
        is_long=True,
        cfg=cfg,
    )
    return result.blocked, result.line, result.line_price
