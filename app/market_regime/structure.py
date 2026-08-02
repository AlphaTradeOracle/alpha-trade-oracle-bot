"""Heuristic market-structure helpers (BOS/CHoCH/FVG/OB/liquidity).

These are intentionally simplified — not TradingView Smart-Money parity.
They reuse swing detection from ``app.indicators.structure``.
"""

from __future__ import annotations

import pandas as pd

from app.indicators.structure import StructureAnalysis, find_swing_points
from app.market_regime.types import StructureSnapshot


def build_structure_snapshot(
    df: pd.DataFrame,
    base: StructureAnalysis | None = None,
) -> StructureSnapshot:
    """Derive BOS/CHoCH/FVG/OB heuristics from OHLCV + optional base analysis."""
    notes: list[str] = []
    if base is not None:
        notes.extend(base.notes[:6])

    if df is None or len(df) < 30 or not {"high", "low", "close"}.issubset(df.columns):
        return StructureSnapshot(
            higher_highs=bool(base.higher_highs) if base else False,
            higher_lows=bool(base.higher_lows) if base else False,
            lower_highs=bool(base.lower_highs) if base else False,
            lower_lows=bool(base.lower_lows) if base else False,
            support=base.nearest_support if base else None,
            resistance=base.nearest_resistance if base else None,
            notes=tuple(notes),
        )

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    window = min(120, len(df))
    high_w = high.iloc[-window:].reset_index(drop=True)
    low_w = low.iloc[-window:].reset_index(drop=True)
    close_w = close.iloc[-window:].reset_index(drop=True)
    swings = find_swing_points(high_w, low_w, left=3, right=3)
    swing_highs = [s for s in swings if s.is_high]
    swing_lows = [s for s in swings if not s.is_high]
    price = float(close_w.iloc[-1])

    hh = base.higher_highs if base else (len(swing_highs) >= 2 and swing_highs[-1].price > swing_highs[-2].price)
    hl = base.higher_lows if base else (len(swing_lows) >= 2 and swing_lows[-1].price > swing_lows[-2].price)
    lh = base.lower_highs if base else (len(swing_highs) >= 2 and swing_highs[-1].price < swing_highs[-2].price)
    ll = base.lower_lows if base else (len(swing_lows) >= 2 and swing_lows[-1].price < swing_lows[-2].price)

    bos_bull = False
    bos_bear = False
    choch_bull = False
    choch_bear = False
    if swing_highs:
        last_high = swing_highs[-1].price
        if price > last_high:
            bos_bull = True
            notes.append("BOS bullish (close above last swing high)")
            if lh or ll:
                choch_bull = True
                notes.append("CHoCH bullish (break against prior bearish structure)")
    if swing_lows:
        last_low = swing_lows[-1].price
        if price < last_low:
            bos_bear = True
            notes.append("BOS bearish (close below last swing low)")
            if hh or hl:
                choch_bear = True
                notes.append("CHoCH bearish (break against prior bullish structure)")

    fvg_bull, fvg_bear = _detect_fvg(high_w, low_w)
    if fvg_bull:
        notes.append("Bullish FVG nearby")
    if fvg_bear:
        notes.append("Bearish FVG nearby")

    ob_high, ob_low = _detect_order_block(high_w, low_w, close_w)
    liq_high = max((s.price for s in swing_highs[-3:]), default=None)
    liq_low = min((s.price for s in swing_lows[-3:]), default=None)

    return StructureSnapshot(
        higher_highs=bool(hh),
        higher_lows=bool(hl),
        lower_highs=bool(lh),
        lower_lows=bool(ll),
        support=base.nearest_support if base else (liq_low if liq_low is not None and liq_low < price else None),
        resistance=(
            base.nearest_resistance if base else (liq_high if liq_high is not None and liq_high > price else None)
        ),
        liquidity_high=liq_high,
        liquidity_low=liq_low,
        order_block_high=ob_high,
        order_block_low=ob_low,
        fvg_bullish=fvg_bull,
        fvg_bearish=fvg_bear,
        bos_bullish=bos_bull,
        bos_bearish=bos_bear,
        choch_bullish=choch_bull,
        choch_bearish=choch_bear,
        notes=tuple(notes[:10]),
    )


def _detect_fvg(high: pd.Series, low: pd.Series) -> tuple[bool, bool]:
    """3-candle fair-value-gap in the last ~20 bars."""
    n = len(high)
    if n < 5:
        return False, False
    bull = bear = False
    start = max(2, n - 20)
    for i in range(start, n):
        # Bullish FVG: candle i low > candle i-2 high
        if float(low.iloc[i]) > float(high.iloc[i - 2]):
            bull = True
        # Bearish FVG: candle i high < candle i-2 low
        if float(high.iloc[i]) < float(low.iloc[i - 2]):
            bear = True
    return bull, bear


def _detect_order_block(
    high: pd.Series, low: pd.Series, close: pd.Series
) -> tuple[float | None, float | None]:
    """Last impulsive opposite candle before a breakout — crude OB proxy."""
    n = len(close)
    if n < 8:
        return None, None
    # Look for a strong up-move after a down candle → bullish OB
    for i in range(n - 2, max(3, n - 30), -1):
        body_prev = float(close.iloc[i - 1]) - float(close.iloc[i - 2])
        move = float(close.iloc[i]) - float(close.iloc[i - 1])
        if body_prev < 0 and move > abs(body_prev) * 1.5:
            return float(high.iloc[i - 1]), float(low.iloc[i - 1])
        if body_prev > 0 and move < -abs(body_prev) * 1.5:
            return float(high.iloc[i - 1]), float(low.iloc[i - 1])
    return None, None
