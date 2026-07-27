"""Bewertung der einzelnen Score-Kategorien.

Jede Funktion liefert einen Rohwert in [-100, +100] und einen Begruendungstext.
Positive Werte sind bullisch, negative baerisch. Die Funktionen sind rein und
einzeln testbar.
"""

from __future__ import annotations

from app.core.enums import StructureState
from app.indicators.engine import IndicatorSet

#: ADX-Schwellen: unter 20 gilt ein Markt als trendlos, ab 25 als trendstark.
ADX_TRENDLESS = 20.0
ADX_TRENDING = 25.0

#: RSI-Schwellen.
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0

#: Volumenverhaeltnis, ab dem von einer Volumenspitze gesprochen wird.
VOLUME_SPIKE_RATIO = 1.8

#: Zielband fuer ATR in Prozent: darunter zu ruhig, darueber zu riskant.
ATR_IDEAL_MIN = 0.8
ATR_IDEAL_MAX = 5.0


def score_trend(indicators: IndicatorSet) -> tuple[float, str]:
    """Trendbewertung aus EMA-Staffelung, EMA200-Lage und Supertrend.

    ADX skaliert das Ergebnis: eine EMA-Staffelung im Seitwaertsmarkt ist
    deutlich weniger wert als dieselbe Staffelung in einem starken Trend.
    """
    score = 0.0
    notes: list[str] = []
    price = indicators.close_price

    if indicators.ema_9 is not None and indicators.ema_20 is not None:
        if indicators.ema_9 > indicators.ema_20:
            score += 15.0
            notes.append("EMA9 ueber EMA20")
        else:
            score -= 15.0
            notes.append("EMA9 unter EMA20")

    if indicators.ema_20 is not None and indicators.ema_50 is not None:
        if indicators.ema_20 > indicators.ema_50:
            score += 20.0
            notes.append("EMA20 ueber EMA50")
        else:
            score -= 20.0
            notes.append("EMA20 unter EMA50")

    if indicators.ema_200 is not None:
        if price > indicators.ema_200:
            score += 25.0
            notes.append("Kurs ueber EMA200")
        else:
            score -= 25.0
            notes.append("Kurs unter EMA200")

    if indicators.sma_50 is not None and indicators.sma_200 is not None:
        if indicators.sma_50 > indicators.sma_200:
            score += 15.0
            notes.append("SMA50 ueber SMA200 (Golden-Cross-Lage)")
        else:
            score -= 15.0
            notes.append("SMA50 unter SMA200 (Death-Cross-Lage)")

    if indicators.supertrend_direction is not None:
        if indicators.supertrend_direction > 0:
            score += 25.0
            notes.append("Supertrend bullisch")
        else:
            score -= 25.0
            notes.append("Supertrend baerisch")

    # ADX-Skalierung: verstaerkt klare Trends, daempft Seitwaertsphasen.
    if indicators.adx_14 is not None:
        if indicators.adx_14 >= ADX_TRENDING:
            score *= 1.2
            notes.append(f"ADX {indicators.adx_14:.1f} bestaetigt einen Trend")
        elif indicators.adx_14 < ADX_TRENDLESS:
            score *= 0.6
            notes.append(f"ADX {indicators.adx_14:.1f} zeigt keinen klaren Trend")

    return _clamp(score), "; ".join(notes) or "Keine Trenddaten verfuegbar"


def score_momentum(indicators: IndicatorSet) -> tuple[float, str]:
    """Momentum aus RSI-Lage und -Neigung, MACD-Histogramm, StochRSI und ROC."""
    score = 0.0
    notes: list[str] = []

    if indicators.rsi_14 is not None:
        rsi_value = indicators.rsi_14
        if rsi_value >= RSI_OVERBOUGHT:
            # Ueberkauft: kurzfristig bullisch, aber Rueckschlagrisiko.
            score += 10.0
            notes.append(f"RSI {rsi_value:.1f} ueberkauft (Rueckschlagrisiko)")
        elif rsi_value <= RSI_OVERSOLD:
            score -= 10.0
            notes.append(f"RSI {rsi_value:.1f} ueberverkauft (Erholungspotenzial)")
        elif rsi_value > 55.0:
            score += 25.0
            notes.append(f"RSI {rsi_value:.1f} bullisch ohne Ueberhitzung")
        elif rsi_value < 45.0:
            score -= 25.0
            notes.append(f"RSI {rsi_value:.1f} baerisch")
        else:
            notes.append(f"RSI {rsi_value:.1f} neutral")

        if indicators.rsi_previous is not None:
            delta = rsi_value - indicators.rsi_previous
            if abs(delta) >= 1.0:
                score += 10.0 if delta > 0 else -10.0
                notes.append("RSI steigend" if delta > 0 else "RSI fallend")

    if indicators.macd_histogram is not None:
        if indicators.macd_histogram > 0:
            score += 25.0
            notes.append("MACD-Histogramm positiv")
        else:
            score -= 25.0
            notes.append("MACD-Histogramm negativ")

        if indicators.macd_histogram_previous is not None:
            growing = abs(indicators.macd_histogram) > abs(indicators.macd_histogram_previous)
            if growing:
                score += 10.0 if indicators.macd_histogram > 0 else -10.0
                notes.append("MACD-Momentum nimmt zu")

    if indicators.stoch_rsi_k is not None:
        k = indicators.stoch_rsi_k
        if k > 80.0:
            notes.append(f"StochRSI {k:.0f} im oberen Extrembereich")
            score -= 5.0
        elif k < 20.0:
            notes.append(f"StochRSI {k:.0f} im unteren Extrembereich")
            score += 5.0
        elif k > 50.0:
            score += 15.0
        else:
            score -= 15.0

    if indicators.roc_14 is not None:
        if indicators.roc_14 > 1.0:
            score += 15.0
            notes.append(f"ROC {indicators.roc_14:+.1f}% positiv")
        elif indicators.roc_14 < -1.0:
            score -= 15.0
            notes.append(f"ROC {indicators.roc_14:+.1f}% negativ")

    return _clamp(score), "; ".join(notes) or "Keine Momentumdaten verfuegbar"


def score_volume(indicators: IndicatorSet) -> tuple[float, str]:
    """Volumenbewertung.

    Volumen hat keine eigene Richtung — es bestaetigt oder widerlegt eine
    Kursbewegung. Das Vorzeichen wird daher aus der Trendrichtung uebernommen.
    """
    score = 0.0
    notes: list[str] = []
    trend_sign = (
        1.0
        if indicators.trend_direction.value == "BULLISH"
        else (-1.0 if indicators.trend_direction.value == "BEARISH" else 0.0)
    )

    if indicators.volume_ratio is not None:
        ratio = indicators.volume_ratio
        if ratio >= VOLUME_SPIKE_RATIO:
            score += 45.0 * trend_sign
            notes.append(f"Volumenspitze ({ratio:.1f}x Durchschnitt)")
        elif ratio >= 1.2:
            score += 25.0 * trend_sign
            notes.append(f"Volumen ueber Durchschnitt ({ratio:.1f}x)")
        elif ratio < 0.7:
            # Dünnes Volumen schwaecht die Bewegung, unabhaengig von der Richtung.
            score -= 20.0 * trend_sign
            notes.append(f"Unterdurchschnittliches Volumen ({ratio:.1f}x)")
        else:
            notes.append(f"Volumen normal ({ratio:.1f}x)")

    if indicators.obv_slope is not None:
        slope = indicators.obv_slope
        if slope > 0.02:
            score += 35.0
            notes.append("OBV steigend (Akkumulation)")
        elif slope < -0.02:
            score -= 35.0
            notes.append("OBV fallend (Distribution)")
        else:
            notes.append("OBV seitwaerts")

    if indicators.structure.breakout_up and indicators.volume_ratio:
        if indicators.volume_ratio >= VOLUME_SPIKE_RATIO:
            score += 20.0
            notes.append("Ausbruch nach oben mit Volumenbestaetigung")
        else:
            score -= 15.0
            notes.append("Ausbruch nach oben ohne Volumenbestaetigung")
    if indicators.structure.breakout_down and indicators.volume_ratio:
        if indicators.volume_ratio >= VOLUME_SPIKE_RATIO:
            score -= 20.0
            notes.append("Ausbruch nach unten mit Volumenbestaetigung")
        else:
            score += 15.0
            notes.append("Ausbruch nach unten ohne Volumenbestaetigung")

    return _clamp(score), "; ".join(notes) or "Keine Volumendaten verfuegbar"


def score_volatility(indicators: IndicatorSet) -> tuple[float, str]:
    """Volatilitaetsbewertung.

    Bewertet die Handelbarkeit, nicht die Richtung: ein Markt im ATR-Zielband
    erlaubt sinnvolle Stops, ein extrem volatiler nicht. Das Vorzeichen folgt
    daher der Trendrichtung.
    """
    score = 0.0
    notes: list[str] = []
    trend_sign = (
        1.0
        if indicators.trend_direction.value == "BULLISH"
        else (-1.0 if indicators.trend_direction.value == "BEARISH" else 0.0)
    )

    if indicators.atr_percent is not None:
        atr_pct = indicators.atr_percent
        if ATR_IDEAL_MIN <= atr_pct <= ATR_IDEAL_MAX:
            score += 50.0 * trend_sign
            notes.append(f"ATR {atr_pct:.2f}% im gut handelbaren Bereich")
        elif atr_pct > ATR_IDEAL_MAX:
            score -= 40.0
            notes.append(f"ATR {atr_pct:.2f}% erhoeht (weite Stops erforderlich)")
        else:
            score -= 15.0
            notes.append(f"ATR {atr_pct:.2f}% sehr niedrig (wenig Bewegung)")

    if (
        indicators.bb_width is not None
        and indicators.bb_width_average is not None
        and indicators.bb_width_average > 0
    ):
        relative = indicators.bb_width / indicators.bb_width_average
        if relative < 0.7:
            # Squeeze: Ausbruch wahrscheinlich, Richtung aber offen.
            notes.append("Bollinger-Squeeze (Ausbruch moeglich)")
        elif relative > 1.5:
            score += 25.0 * trend_sign
            notes.append("Bollinger-Baender expandieren (Bewegung laeuft)")

    return _clamp(score), "; ".join(notes) or "Keine Volatilitaetsdaten verfuegbar"


def score_structure(indicators: IndicatorSet) -> tuple[float, str]:
    """Marktstruktur: HH/HL bzw. LH/LL, Breakouts, Fehlausbrueche, Divergenzen."""
    score = 0.0
    notes: list[str] = []
    structure = indicators.structure

    if structure.state == StructureState.HH_HL:
        score += 40.0
        notes.append("Struktur: hoehere Hochs und hoehere Tiefs")
    elif structure.state == StructureState.LH_LL:
        score -= 40.0
        notes.append("Struktur: tiefere Hochs und tiefere Tiefs")
    else:
        notes.append("Struktur: Seitwaertsbereich")

    if structure.breakout_up:
        score += 25.0
        notes.append("Ausbruch ueber Widerstand")
    if structure.breakout_down:
        score -= 25.0
        notes.append("Ausbruch unter Support")
    if structure.failed_breakout_up:
        score -= 30.0
        notes.append("Fehlausbruch nach oben")
    if structure.failed_breakout_down:
        score += 30.0
        notes.append("Fehlausbruch nach unten")

    if structure.bullish_divergence:
        score += 20.0
        notes.append("Bullische Divergenz")
    if structure.bearish_divergence:
        score -= 20.0
        notes.append("Baerische Divergenz")

    # Naehe zu Leveln: direkt unter einem Widerstand ist eine Long-Position
    # schlechter, direkt ueber einem Support besser.
    price = indicators.close_price
    atr_value = indicators.atr_14
    if atr_value and atr_value > 0:
        if structure.nearest_resistance is not None:
            distance = (structure.nearest_resistance - price) / atr_value
            if distance < 0.5:
                score -= 15.0
                notes.append("Widerstand unmittelbar oberhalb")
        if structure.nearest_support is not None:
            distance = (price - structure.nearest_support) / atr_value
            if distance < 0.5:
                score += 15.0
                notes.append("Support unmittelbar unterhalb")

    return _clamp(score), "; ".join(notes) or "Keine Strukturdaten verfuegbar"


def score_risk_reward(achieved_ratio: float, minimum_ratio: float) -> tuple[float, str]:
    """Bewertet das erreichte R:R relativ zum Minimum.

    Ein R:R genau am Minimum ergibt 0 (neutral), doppeltes Minimum ergibt +100.
    Der Wert ist immer nicht-negativ, weil ein schlechtes R:R ueber die
    NO_TRADE-Regel abgefangen wird, nicht ueber ein negatives Vorzeichen.
    """
    if minimum_ratio <= 0:
        return 0.0, "Kein Mindest-R:R konfiguriert"
    if achieved_ratio <= 0:
        return -100.0, "Kein gueltiges Chance-Risiko-Verhaeltnis berechenbar"

    relative = (achieved_ratio - minimum_ratio) / minimum_ratio
    score = _clamp(relative * 100.0)
    return score, f"Chance-Risiko-Verhaeltnis {achieved_ratio:.2f} (Minimum {minimum_ratio:.2f})"


def _clamp(value: float, lower: float = -100.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))
