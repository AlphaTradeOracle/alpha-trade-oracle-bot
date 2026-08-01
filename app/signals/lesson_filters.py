"""Post-signal skip rules learned from WUSDT-style losing shorts.

These gates sit *after* normal score / ADX / RR filters. They do not change
scoring — they only decide whether an otherwise actionable signal is taken.
"""

from __future__ import annotations

from collections.abc import Collection

from app.signals.types import SignalResult

#: Named rule keys used by BacktestConfig / compare scripts.
LESSON_RULE_BULLISH_DIV = "bullish_div"
LESSON_RULE_VOL_LT_0_5 = "vol_lt_0_5"
LESSON_RULE_WEAK_VOL = "weak_vol"
LESSON_RULE_NO_VOL_CONFIRM = "no_vol_confirm"
LESSON_RULE_RSI_RISING = "rsi_rising"
LESSON_RULE_BB_SQUEEZE = "bb_squeeze"

COMBO_CORE: tuple[str, ...] = (
    LESSON_RULE_BULLISH_DIV,
    LESSON_RULE_NO_VOL_CONFIRM,
    LESSON_RULE_RSI_RISING,
)
COMBO_FULL: tuple[str, ...] = (
    LESSON_RULE_BULLISH_DIV,
    LESSON_RULE_WEAK_VOL,
    LESSON_RULE_NO_VOL_CONFIRM,
    LESSON_RULE_RSI_RISING,
    LESSON_RULE_VOL_LT_0_5,
    LESSON_RULE_BB_SQUEEZE,
)


def _primary_indicators(signal: SignalResult):
    assessment = signal.assessments.get(signal.primary_timeframe)
    if assessment is None and signal.assessments:
        assessment = next(iter(signal.assessments.values()))
    return assessment.indicators if assessment is not None else None


def _has_bullish_divergence(signal: SignalResult) -> bool:
    for text in signal.counter_arguments:
        low = text.lower()
        if "bullische divergenz" in low or "bullish divergence" in low:
            return True
    for assessment in signal.assessments.values():
        if assessment.indicators.structure.bullish_divergence:
            return True
    return False


def _rsi_rising(signal: SignalResult) -> bool:
    indicators = _primary_indicators(signal)
    if indicators is None:
        return False
    if indicators.rsi_14 is None or indicators.rsi_previous is None:
        # Fallback: component / reason text from scoring notes
        blob = " ".join(c.detail for c in signal.components).lower()
        blob += " " + " ".join(signal.reasons).lower()
        return "rsi rising" in blob or "rsi steigend" in blob
    return indicators.rsi_14 > indicators.rsi_previous


def _bb_squeeze(signal: SignalResult) -> bool:
    indicators = _primary_indicators(signal)
    if indicators is None:
        return False
    width = indicators.bb_width
    avg = indicators.bb_width_average
    if width is not None and avg is not None and avg > 0:
        return (width / avg) < 0.7
    blob = " ".join(c.detail for c in signal.components).lower()
    return "bollinger squeeze" in blob or "bollinger-squeeze" in blob


def _volume_ratio(signal: SignalResult) -> float | None:
    indicators = _primary_indicators(signal)
    if indicators is None:
        return None
    return indicators.volume_ratio


def _breakout_down(signal: SignalResult) -> bool:
    indicators = _primary_indicators(signal)
    if indicators is None:
        return False
    return bool(indicators.structure.breakout_down)


def lesson_skip_reason(
    signal: SignalResult,
    rules: Collection[str],
) -> str | None:
    """Return the first matching skip reason, or None if the signal may trade."""
    if not rules:
        return None

    rule_set = set(rules)
    is_short = signal.direction.is_short
    vol = _volume_ratio(signal)

    if is_short and LESSON_RULE_BULLISH_DIV in rule_set and _has_bullish_divergence(signal):
        return "short_bullish_divergence"

    if is_short and LESSON_RULE_VOL_LT_0_5 in rule_set and vol is not None and vol < 0.5:
        return "short_volume_ratio_lt_0_5"

    if is_short and LESSON_RULE_WEAK_VOL in rule_set and vol is not None and vol < 1.0:
        return "short_weak_volume"

    if (
        is_short
        and LESSON_RULE_NO_VOL_CONFIRM in rule_set
        and _breakout_down(signal)
        and vol is not None
        and vol < 1.8
    ):
        return "short_no_volume_confirmation"

    if is_short and LESSON_RULE_RSI_RISING in rule_set and _rsi_rising(signal):
        return "short_rsi_rising"

    if LESSON_RULE_BB_SQUEEZE in rule_set and _bb_squeeze(signal):
        return "bb_squeeze"

    return None
