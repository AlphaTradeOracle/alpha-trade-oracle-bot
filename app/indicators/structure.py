"""Marktstruktur: Swing-Punkte, Support/Resistance, Breakouts, Divergenzen.

Alle Funktionen arbeiten ausschliesslich auf abgeschlossenen Kerzen und blicken
nie in die Zukunft. Ein Swing-Hoch bei Index ``i`` gilt erst als bestaetigt, wenn
``left`` Kerzen davor und ``right`` Kerzen danach vorliegen — genau deshalb liefert
:func:`find_swing_points` fuer die letzten ``right`` Kerzen keine Punkte.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.core.enums import StructureState


@dataclass(frozen=True)
class SwingPoint:
    index: int
    price: float
    is_high: bool


@dataclass
class StructureAnalysis:
    """Ergebnis der Marktstrukturanalyse eines Timeframes."""

    state: StructureState = StructureState.RANGE
    supports: list[float] = field(default_factory=list)
    resistances: list[float] = field(default_factory=list)
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    #: Aktueller Preis der fallenden Widerstands-Diagonale (Lower Highs), falls frisch.
    falling_resistance: float | None = None
    #: Aktueller Preis der steigenden Support-Diagonale (Higher Lows), falls frisch.
    rising_support: float | None = None
    breakout_up: bool = False
    breakout_down: bool = False
    failed_breakout_up: bool = False
    failed_breakout_down: bool = False
    higher_highs: bool = False
    higher_lows: bool = False
    lower_highs: bool = False
    lower_lows: bool = False
    bullish_divergence: bool = False
    bearish_divergence: bool = False
    notes: list[str] = field(default_factory=list)


def find_swing_points(
    high: pd.Series, low: pd.Series, left: int = 3, right: int = 3
) -> list[SwingPoint]:
    """Fraktale Swing-Hochs und -Tiefs.

    Ein Swing-Hoch liegt vor, wenn das Hoch der Kerze strikt hoeher ist als die
    ``left`` Hochs davor und mindestens so hoch wie die ``right`` Hochs danach.
    """
    if left < 1 or right < 1:
        raise ValueError("left und right muessen mindestens 1 sein")

    highs = high.to_numpy(dtype=float)
    lows = low.to_numpy(dtype=float)
    n = len(highs)
    points: list[SwingPoint] = []

    for i in range(left, n - right):
        window_left_high = highs[i - left : i]
        window_right_high = highs[i + 1 : i + 1 + right]
        if highs[i] > window_left_high.max() and highs[i] >= window_right_high.max():
            points.append(SwingPoint(index=i, price=float(highs[i]), is_high=True))
            continue

        window_left_low = lows[i - left : i]
        window_right_low = lows[i + 1 : i + 1 + right]
        if lows[i] < window_left_low.min() and lows[i] <= window_right_low.min():
            points.append(SwingPoint(index=i, price=float(lows[i]), is_high=False))

    return points


def cluster_levels(prices: list[float], tolerance_percent: float = 0.5) -> list[float]:
    """Nahe beieinander liegende Level zu einem Level zusammenfassen.

    Drei Swing-Hochs bei 67.100, 67.150 und 67.200 sind faktisch ein Widerstand
    und sollen nicht als drei getrennte Level gezaehlt werden.
    """
    if not prices:
        return []

    ordered = sorted(prices)
    clusters: list[list[float]] = [[ordered[0]]]

    for price in ordered[1:]:
        reference = clusters[-1][-1]
        if reference > 0 and abs(price - reference) / reference * 100.0 <= tolerance_percent:
            clusters[-1].append(price)
        else:
            clusters.append([price])

    return [float(np.mean(cluster)) for cluster in clusters]


def analyze_structure(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    rsi_values: pd.Series | None = None,
    *,
    swing_left: int = 3,
    swing_right: int = 3,
    lookback: int = 120,
    atr_value: float | None = None,
    trendline_lookback: int = 40,
) -> StructureAnalysis:
    """Vollstaendige Marktstrukturanalyse fuer den aktuellen Kursstand."""
    # Local import avoids a circular dependency at module load.
    from app.indicators.trendlines import TrendlineDetectConfig, fit_falling_resistance, fit_rising_support

    result = StructureAnalysis()
    if len(close) < swing_left + swing_right + 10:
        result.notes.append("Zu wenige Kerzen fuer eine Strukturanalyse")
        return result

    window = min(lookback, len(close))
    high_w = high.iloc[-window:].reset_index(drop=True)
    low_w = low.iloc[-window:].reset_index(drop=True)
    close_w = close.iloc[-window:].reset_index(drop=True)

    swings = find_swing_points(high_w, low_w, swing_left, swing_right)
    swing_highs = [s for s in swings if s.is_high]
    swing_lows = [s for s in swings if not s.is_high]

    current_price = float(close_w.iloc[-1])

    # --- Support- und Resistance-Level -----------------------------------
    result.resistances = [
        lvl for lvl in cluster_levels([s.price for s in swing_highs]) if lvl > current_price
    ]
    result.supports = [
        lvl for lvl in cluster_levels([s.price for s in swing_lows]) if lvl < current_price
    ]
    result.nearest_resistance = min(result.resistances) if result.resistances else None
    result.nearest_support = max(result.supports) if result.supports else None

    # --- Trendstruktur ueber die letzten Swings ---------------------------
    if len(swing_highs) >= 2:
        result.higher_highs = swing_highs[-1].price > swing_highs[-2].price
        result.lower_highs = swing_highs[-1].price < swing_highs[-2].price
    if len(swing_lows) >= 2:
        result.higher_lows = swing_lows[-1].price > swing_lows[-2].price
        result.lower_lows = swing_lows[-1].price < swing_lows[-2].price

    if result.higher_highs and result.higher_lows:
        result.state = StructureState.HH_HL
        result.notes.append("Hoehere Hochs und hoehere Tiefs")
    elif result.lower_highs and result.lower_lows:
        result.state = StructureState.LH_LL
        result.notes.append("Tiefere Hochs und tiefere Tiefs")
    else:
        result.state = StructureState.RANGE
        result.notes.append("Keine eindeutige Trendstruktur (Seitwaertsbereich)")

    # --- Breakouts und Fehlausbrueche ------------------------------------
    # Referenz ist das letzte bestaetigte Swing-Level VOR dem Ausbruch, damit ein
    # Ausbruch nicht gegen sich selbst geprueft wird.
    prior_high = max((s.price for s in swing_highs), default=None)
    prior_low = min((s.price for s in swing_lows), default=None)
    recent_closes = close_w.iloc[-5:]

    if prior_high is not None and swing_highs:
        last_high = swing_highs[-1].price
        if current_price > last_high:
            result.breakout_up = True
            result.notes.append("Ausbruch ueber das letzte Swing-Hoch")
        elif (recent_closes > last_high).any() and current_price <= last_high:
            result.failed_breakout_up = True
            result.notes.append("Fehlausbruch nach oben (Rueckfall unter das Hoch)")

    if prior_low is not None and swing_lows:
        last_low = swing_lows[-1].price
        if current_price < last_low:
            result.breakout_down = True
            result.notes.append("Ausbruch unter das letzte Swing-Tief")
        elif (recent_closes < last_low).any() and current_price >= last_low:
            result.failed_breakout_down = True
            result.notes.append("Fehlausbruch nach unten (Rueckkehr ueber das Tief)")

    # --- Divergenzen -----------------------------------------------------
    if rsi_values is not None and len(rsi_values) >= window:
        rsi_w = rsi_values.iloc[-window:].reset_index(drop=True)
        result.bullish_divergence = _has_bullish_divergence(swing_lows, rsi_w)
        result.bearish_divergence = _has_bearish_divergence(swing_highs, rsi_w)
        if result.bullish_divergence:
            result.notes.append("Moegliche bullische RSI-Divergenz")
        if result.bearish_divergence:
            result.notes.append("Moegliche baerische RSI-Divergenz")

    # --- Naehe zu Leveln --------------------------------------------------
    if atr_value and atr_value > 0:
        if result.nearest_resistance is not None:
            distance = (result.nearest_resistance - current_price) / atr_value
            if distance < 0.5:
                result.notes.append("Kurs unmittelbar unter einem Widerstand")
        if result.nearest_support is not None:
            distance = (current_price - result.nearest_support) / atr_value
            if distance < 0.5:
                result.notes.append("Kurs unmittelbar ueber einem Support")

    # --- Diagonale Trendlinien (frisch im Trendline-Lookback) -------------
    tl_cfg = TrendlineDetectConfig(
        lookback=max(10, int(trendline_lookback)),
        min_points=2,
        swing_left=swing_left,
        swing_right=swing_right,
    )
    tl_start = max(0, len(high_w) - tl_cfg.lookback)
    tl_swings = [s for s in swings if s.index >= tl_start]
    eval_idx = len(high_w) - 1
    falling = fit_falling_resistance(
        tl_swings, eval_idx=eval_idx, atr=atr_value, cfg=tl_cfg
    )
    rising = fit_rising_support(
        tl_swings, eval_idx=eval_idx, atr=atr_value, cfg=tl_cfg
    )
    if falling is not None:
        result.falling_resistance = falling.price_at(eval_idx)
        result.notes.append(
            f"Fallender Widerstand @ {result.falling_resistance:.6g} (r2={falling.r2:.2f})"
        )
    if rising is not None:
        result.rising_support = rising.price_at(eval_idx)
        result.notes.append(
            f"Steigender Support @ {result.rising_support:.6g} (r2={rising.r2:.2f})"
        )

    return result


def _has_bullish_divergence(swing_lows: list[SwingPoint], rsi_values: pd.Series) -> bool:
    """Kurs macht ein tieferes Tief, der RSI aber ein hoeheres — Kaufdruck baut sich auf."""
    if len(swing_lows) < 2:
        return False
    last, prev = swing_lows[-1], swing_lows[-2]
    if last.price >= prev.price:
        return False
    rsi_last = _value_at(rsi_values, last.index)
    rsi_prev = _value_at(rsi_values, prev.index)
    if rsi_last is None or rsi_prev is None:
        return False
    return rsi_last > rsi_prev


def _has_bearish_divergence(swing_highs: list[SwingPoint], rsi_values: pd.Series) -> bool:
    """Kurs macht ein hoeheres Hoch, der RSI aber ein tieferes — Momentum laesst nach."""
    if len(swing_highs) < 2:
        return False
    last, prev = swing_highs[-1], swing_highs[-2]
    if last.price <= prev.price:
        return False
    rsi_last = _value_at(rsi_values, last.index)
    rsi_prev = _value_at(rsi_values, prev.index)
    if rsi_last is None or rsi_prev is None:
        return False
    return rsi_last < rsi_prev


def _value_at(series: pd.Series, position: int) -> float | None:
    if position < 0 or position >= len(series):
        return None
    value = series.iloc[position]
    if pd.isna(value):
        return None
    return float(value)
