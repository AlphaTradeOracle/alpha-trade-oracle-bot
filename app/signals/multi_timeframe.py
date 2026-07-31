"""Multi-Timeframe-Analyse.

Rollenverteilung: 1d bestimmt den Makrotrend, 4h bestaetigt die Richtung,
1h liefert das Setup, 15m optimiert das Timing. Fehlt ein Timeframe, wird sein
Gewicht proportional auf die vorhandenen verteilt.
"""

from __future__ import annotations

from app.core.enums import MarketPhase, TrendDirection
from app.indicators.engine import IndicatorSet
from app.signals.scoring import (
    ADX_TRENDING,
    score_momentum,
    score_structure,
    score_trend,
    score_volatility,
    score_volume,
)
from app.signals.types import TimeframeAssessment
from app.strategies.weights import TIMEFRAME_ROLE_WEIGHTS


def assess_timeframes(
    indicator_sets: dict[str, IndicatorSet],
) -> dict[str, TimeframeAssessment]:
    """Jeden Timeframe einzeln bewerten und die Rollengewichte normalisieren."""
    if not indicator_sets:
        return {}

    raw_weights = {
        timeframe: TIMEFRAME_ROLE_WEIGHTS.get(timeframe, 0.1) for timeframe in indicator_sets
    }
    total = sum(raw_weights.values())
    # Normalisierung: fehlende Timeframes verschieben ihr Gewicht auf die uebrigen.
    normalized = {tf: (w / total if total > 0 else 0.0) for tf, w in raw_weights.items()}

    assessments: dict[str, TimeframeAssessment] = {}
    for timeframe, indicators in indicator_sets.items():
        trend_score, trend_note = score_trend(indicators)
        momentum_score, momentum_note = score_momentum(indicators)
        volume_score, volume_note = score_volume(indicators)
        volatility_score, volatility_note = score_volatility(indicators)
        structure_score, structure_note = score_structure(indicators)

        assessments[timeframe] = TimeframeAssessment(
            timeframe=timeframe,
            role_weight=normalized[timeframe],
            indicators=indicators,
            trend_score=trend_score,
            momentum_score=momentum_score,
            volume_score=volume_score,
            volatility_score=volatility_score,
            structure_score=structure_score,
            notes=[trend_note, momentum_note, volume_note, volatility_note, structure_note],
        )

    return assessments


def aggregate_category(assessments: dict[str, TimeframeAssessment], attribute: str) -> float:
    """Eine Kategorie ueber alle Timeframes rollengewichtet zusammenfassen."""
    if not assessments:
        return 0.0
    return sum(
        getattr(assessment, attribute) * assessment.role_weight
        for assessment in assessments.values()
    )


def multi_timeframe_agreement(
    assessments: dict[str, TimeframeAssessment],
) -> tuple[float, str]:
    """Uebereinstimmung der Timeframes als Rohwert in [-100, +100].

    Es zaehlt nicht nur der Mittelwert, sondern auch die Einigkeit: wenn alle
    Timeframes dasselbe Vorzeichen haben, wird das Ergebnis verstaerkt. Bei
    Widerspruch zwischen Makro- und Setup-Timeframe wird es gedaempft.
    """
    if not assessments:
        return 0.0, "No timeframes available"

    weighted_sum = sum(
        assessment.directional_score * assessment.role_weight for assessment in assessments.values()
    )

    signs = [
        1
        if assessment.directional_score > 15
        else (-1 if assessment.directional_score < -15 else 0)
        for assessment in assessments.values()
    ]
    non_neutral = [s for s in signs if s != 0]

    notes: list[str] = []
    if non_neutral and all(s > 0 for s in non_neutral):
        weighted_sum *= 1.15
        notes.append("All meaningful timeframes bullish")
    elif non_neutral and all(s < 0 for s in non_neutral):
        weighted_sum *= 1.15
        notes.append("All meaningful timeframes bearish")
    elif 1 in non_neutral and -1 in non_neutral:
        weighted_sum *= 0.6
        notes.append("Timeframes disagree")

    macro = assessments.get("1d")
    setup = assessments.get("1h")
    if macro is not None and setup is not None:
        macro_dir = macro.indicators.trend_direction
        setup_dir = setup.indicators.trend_direction
        if macro_dir != TrendDirection.NEUTRAL and setup_dir != TrendDirection.NEUTRAL:
            if macro_dir == setup_dir:
                notes.append(f"Macro trend (1d) and setup (1h) both {macro_dir.value.lower()}")
            else:
                weighted_sum *= 0.75
                notes.append("Setup runs against the macro trend (1d)")

    detail = "; ".join(notes) if notes else "Timeframes without a clear bias"
    return max(-100.0, min(100.0, weighted_sum)), detail


def determine_market_phase(
    assessments: dict[str, TimeframeAssessment], primary_timeframe: str
) -> MarketPhase:
    """Marktphase primaer aus dem Setup-Timeframe ableiten.

    Extreme Volatilitaet ueberschreibt die Trendeinordnung, weil sie das
    Risikoprofil dominiert.
    """
    reference = assessments.get(primary_timeframe) or next(iter(assessments.values()), None)
    if reference is None:
        return MarketPhase.RANGE

    indicators = reference.indicators
    if indicators.atr_percent is not None and indicators.atr_percent > 8.0:
        return MarketPhase.VOLATILE

    adx_value = indicators.adx_14
    trend = indicators.trend_direction

    if adx_value is not None and adx_value >= ADX_TRENDING:
        if trend == TrendDirection.BULLISH:
            return MarketPhase.UPTREND
        if trend == TrendDirection.BEARISH:
            return MarketPhase.DOWNTREND

    return MarketPhase.RANGE


def describe_timeframe_trends(assessments: dict[str, TimeframeAssessment]) -> list[str]:
    """Kurzbeschreibung je Timeframe fuer die Telegram-Nachricht."""
    order = ["1d", "4h", "1h", "15m"]
    ordered = sorted(
        assessments.values(),
        key=lambda a: order.index(a.timeframe) if a.timeframe in order else len(order),
    )

    labels = {
        TrendDirection.BULLISH: "bullish",
        TrendDirection.BEARISH: "bearish",
        TrendDirection.NEUTRAL: "neutral",
    }
    return [
        f"{assessment.timeframe} {labels[assessment.indicators.trend_direction]}"
        for assessment in ordered
    ]
