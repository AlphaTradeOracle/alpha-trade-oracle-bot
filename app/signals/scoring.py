"""Score category evaluation.

Each function returns a raw value in [-100, +100] and a reason string.
Positive values are bullish, negative are bearish. Functions are pure and
individually testable.
"""

from __future__ import annotations

from app.core.enums import StructureState
from app.indicators.engine import IndicatorSet

#: ADX thresholds: below 20 = trendless, from 25 = trending.
ADX_TRENDLESS = 20.0
ADX_TRENDING = 25.0

#: RSI thresholds.
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0

#: Volume ratio at which a spike is assumed.
VOLUME_SPIKE_RATIO = 1.8

#: Target ATR percent band: below too quiet, above too risky.
ATR_IDEAL_MIN = 0.8
ATR_IDEAL_MAX = 5.0


def score_trend(indicators: IndicatorSet) -> tuple[float, str]:
    """Trend score from EMA stack, EMA200 location, and Supertrend."""
    score = 0.0
    notes: list[str] = []
    price = indicators.close_price

    if indicators.ema_9 is not None and indicators.ema_20 is not None:
        if indicators.ema_9 > indicators.ema_20:
            score += 15.0
            notes.append("EMA9 above EMA20")
        else:
            score -= 15.0
            notes.append("EMA9 below EMA20")

    if indicators.ema_20 is not None and indicators.ema_50 is not None:
        if indicators.ema_20 > indicators.ema_50:
            score += 20.0
            notes.append("EMA20 above EMA50")
        else:
            score -= 20.0
            notes.append("EMA20 below EMA50")

    if indicators.ema_200 is not None:
        if price > indicators.ema_200:
            score += 25.0
            notes.append("Price above EMA200")
        else:
            score -= 25.0
            notes.append("Price below EMA200")

    if indicators.sma_50 is not None and indicators.sma_200 is not None:
        if indicators.sma_50 > indicators.sma_200:
            score += 15.0
            notes.append("SMA50 above SMA200 (golden-cross setup)")
        else:
            score -= 15.0
            notes.append("SMA50 below SMA200 (death-cross setup)")

    if indicators.supertrend_direction is not None:
        if indicators.supertrend_direction > 0:
            score += 25.0
            notes.append("Supertrend bullish")
        else:
            score -= 25.0
            notes.append("Supertrend bearish")

    if indicators.adx_14 is not None:
        if indicators.adx_14 >= ADX_TRENDING:
            score *= 1.2
            notes.append(f"ADX {indicators.adx_14:.1f} confirms a trend")
        elif indicators.adx_14 < ADX_TRENDLESS:
            score *= 0.6
            notes.append(f"ADX {indicators.adx_14:.1f} shows no clear trend")

    return _clamp(score), "; ".join(notes) or "No trend data available"


def score_momentum(indicators: IndicatorSet) -> tuple[float, str]:
    """Momentum from RSI level/slope, MACD histogram, StochRSI, and ROC."""
    score = 0.0
    notes: list[str] = []

    if indicators.rsi_14 is not None:
        rsi_value = indicators.rsi_14
        if rsi_value >= RSI_OVERBOUGHT:
            score += 10.0
            notes.append(f"RSI {rsi_value:.1f} overbought (pullback risk)")
        elif rsi_value <= RSI_OVERSOLD:
            score -= 10.0
            notes.append(f"RSI {rsi_value:.1f} oversold (rebound potential)")
        elif rsi_value > 55.0:
            score += 25.0
            notes.append(f"RSI {rsi_value:.1f} bullish without overheating")
        elif rsi_value < 45.0:
            score -= 25.0
            notes.append(f"RSI {rsi_value:.1f} bearish")
        else:
            notes.append(f"RSI {rsi_value:.1f} neutral")

        if indicators.rsi_previous is not None:
            delta = rsi_value - indicators.rsi_previous
            if abs(delta) >= 1.0:
                score += 10.0 if delta > 0 else -10.0
                notes.append("RSI rising" if delta > 0 else "RSI falling")

    if indicators.macd_histogram is not None:
        if indicators.macd_histogram > 0:
            score += 25.0
            notes.append("MACD histogram positive")
        else:
            score -= 25.0
            notes.append("MACD histogram negative")

        if indicators.macd_histogram_previous is not None:
            growing = abs(indicators.macd_histogram) > abs(indicators.macd_histogram_previous)
            if growing:
                score += 10.0 if indicators.macd_histogram > 0 else -10.0
                notes.append("MACD momentum increasing")

    if indicators.stoch_rsi_k is not None:
        k = indicators.stoch_rsi_k
        if k > 80.0:
            notes.append(f"StochRSI {k:.0f} in upper extreme")
            score -= 5.0
        elif k < 20.0:
            notes.append(f"StochRSI {k:.0f} in lower extreme")
            score += 5.0
        elif k > 50.0:
            score += 15.0
        else:
            score -= 15.0

    if indicators.roc_14 is not None:
        if indicators.roc_14 > 1.0:
            score += 15.0
            notes.append(f"ROC {indicators.roc_14:+.1f}% positive")
        elif indicators.roc_14 < -1.0:
            score -= 15.0
            notes.append(f"ROC {indicators.roc_14:+.1f}% negative")

    return _clamp(score), "; ".join(notes) or "No momentum data available"


def score_volume(indicators: IndicatorSet) -> tuple[float, str]:
    """Volume score — confirms or weakens the move; sign follows trend."""
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
            notes.append(f"Volume spike ({ratio:.1f}x average)")
        elif ratio >= 1.2:
            score += 25.0 * trend_sign
            notes.append(f"Volume above average ({ratio:.1f}x)")
        elif ratio < 0.7:
            score -= 20.0 * trend_sign
            notes.append(f"Below-average volume ({ratio:.1f}x)")
        else:
            notes.append(f"Volume normal ({ratio:.1f}x)")

    if indicators.obv_slope is not None:
        slope = indicators.obv_slope
        if slope > 0.02:
            score += 35.0
            notes.append("OBV rising (accumulation)")
        elif slope < -0.02:
            score -= 35.0
            notes.append("OBV falling (distribution)")
        else:
            notes.append("OBV sideways")

    if indicators.structure.breakout_up and indicators.volume_ratio:
        if indicators.volume_ratio >= VOLUME_SPIKE_RATIO:
            score += 20.0
            notes.append("Upside breakout with volume confirmation")
        else:
            score -= 15.0
            notes.append("Upside breakout without volume confirmation")
    if indicators.structure.breakout_down and indicators.volume_ratio:
        if indicators.volume_ratio >= VOLUME_SPIKE_RATIO:
            score -= 20.0
            notes.append("Downside breakout with volume confirmation")
        else:
            score += 15.0
            notes.append("Downside breakout without volume confirmation")

    return _clamp(score), "; ".join(notes) or "No volume data available"


def score_volatility(indicators: IndicatorSet) -> tuple[float, str]:
    """Volatility / tradeability score; sign follows trend direction."""
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
            notes.append(f"ATR {atr_pct:.2f}% in a tradable range")
        elif atr_pct > ATR_IDEAL_MAX:
            score -= 40.0 * trend_sign
            notes.append(f"ATR {atr_pct:.2f}% elevated (wide stops required)")
        else:
            score -= 15.0 * trend_sign
            notes.append(f"ATR {atr_pct:.2f}% very low (little movement)")

    if (
        indicators.bb_width is not None
        and indicators.bb_width_average is not None
        and indicators.bb_width_average > 0
    ):
        relative = indicators.bb_width / indicators.bb_width_average
        if relative < 0.7:
            notes.append("Bollinger squeeze (breakout possible)")
        elif relative > 1.5:
            score += 25.0 * trend_sign
            notes.append("Bollinger bands expanding (move in progress)")

    return _clamp(score), "; ".join(notes) or "No volatility data available"


def score_structure(indicators: IndicatorSet) -> tuple[float, str]:
    """Market structure: HH/HL or LH/LL, breakouts, failed breaks, divergences."""
    score = 0.0
    notes: list[str] = []
    structure = indicators.structure

    if structure.state == StructureState.HH_HL:
        score += 40.0
        notes.append("Structure: higher highs and higher lows")
    elif structure.state == StructureState.LH_LL:
        score -= 40.0
        notes.append("Structure: lower highs and lower lows")
    else:
        notes.append("Structure: sideways range")

    if structure.breakout_up:
        score += 25.0
        notes.append("Breakout above resistance")
    if structure.breakout_down:
        score -= 25.0
        notes.append("Breakout below support")
    if structure.failed_breakout_up:
        score -= 30.0
        notes.append("Failed upside breakout")
    if structure.failed_breakout_down:
        score += 30.0
        notes.append("Failed downside breakout")

    if structure.bullish_divergence:
        score += 20.0
        notes.append("Bullish divergence")
    if structure.bearish_divergence:
        score -= 20.0
        notes.append("Bearish divergence")

    price = indicators.close_price
    atr_value = indicators.atr_14
    if atr_value and atr_value > 0:
        if structure.nearest_resistance is not None:
            distance = (structure.nearest_resistance - price) / atr_value
            if distance < 0.5:
                score -= 15.0
                notes.append("Resistance immediately overhead")
        if structure.nearest_support is not None:
            distance = (price - structure.nearest_support) / atr_value
            if distance < 0.5:
                score += 15.0
                notes.append("Support immediately below")

    return _clamp(score), "; ".join(notes) or "No structure data available"


def score_risk_reward(achieved_ratio: float, minimum_ratio: float) -> tuple[float, str]:
    """Score achieved R:R relative to the minimum."""
    if minimum_ratio <= 0:
        return 0.0, "No minimum R:R configured"
    if achieved_ratio <= 0:
        return -100.0, "No valid risk-reward ratio available"

    relative = (achieved_ratio - minimum_ratio) / minimum_ratio
    score = _clamp(relative * 100.0)
    return score, f"Risk-reward ratio {achieved_ratio:.2f} (minimum {minimum_ratio:.2f})"


def _clamp(value: float, lower: float = -100.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))
