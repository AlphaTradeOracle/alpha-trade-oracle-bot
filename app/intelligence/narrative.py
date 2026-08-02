"""Market Narrative Engine (KB Part 4)."""

from __future__ import annotations

from app.intelligence.types import MarketNarrative, NarrativeSnapshot, clamp_score
from app.market_regime.types import (
    FearGreedBand,
    FundingStatus,
    MarketBias,
    MarketRegimeSnapshot,
    OiPriceRelation,
    RiskMode,
)


def classify_narrative(regime: MarketRegimeSnapshot | None) -> NarrativeSnapshot:
    if regime is None or not regime.available:
        return NarrativeSnapshot(
            primary=MarketNarrative.UNCERTAIN,
            secondary=(),
            primary_driver="unknown",
            institutional_participation=40.0,
            capital_flow="unknown",
            market_health="neutral",
            continuation_probability=0.5,
            reversal_probability=0.5,
            confidence=30.0,
            reasons=("Market regime unavailable — narrative uncertain.",),
        )

    secondary: list[MarketNarrative] = []
    reasons: list[str] = []
    primary = MarketNarrative.TREND_CONTINUATION
    driver = "mixed_participation"
    institutional = 50.0
    capital_flow = "neutral"
    health = "neutral"
    continuation = 0.55
    reversal = 0.45

    bias = regime.bias
    if bias in (MarketBias.STRONG_BULLISH, MarketBias.BULLISH):
        primary = MarketNarrative.TREND_CONTINUATION
        driver = "institutional_buying" if bias is MarketBias.STRONG_BULLISH else "spot_buying"
        continuation = 0.62 if bias is MarketBias.BULLISH else 0.72
        reversal = 1.0 - continuation
        reasons.append(f"Global bias {bias.label} supports bullish continuation.")
    elif bias in (MarketBias.STRONG_BEARISH, MarketBias.BEARISH):
        primary = MarketNarrative.TREND_CONTINUATION
        driver = "institutional_selling" if bias is MarketBias.STRONG_BEARISH else "spot_selling"
        continuation = 0.62 if bias is MarketBias.BEARISH else 0.72
        reversal = 1.0 - continuation
        reasons.append(f"Global bias {bias.label} supports bearish continuation.")
    else:
        primary = MarketNarrative.RANGE_COMPRESSION
        driver = "liquidity_event"
        continuation = 0.48
        reversal = 0.52
        reasons.append("Neutral global bias — range / compression narrative.")

    if regime.dominance.usdt_risk_mode is RiskMode.RISK_OFF:
        secondary.append(MarketNarrative.MACRO_RISK_OFF)
        capital_flow = "flight_to_stablecoins"
        institutional = max(35.0, institutional - 10.0)
        reasons.append("USDT dominance implies risk-off capital flow.")
    elif regime.dominance.usdt_risk_mode is RiskMode.RISK_ON:
        secondary.append(MarketNarrative.MACRO_RISK_ON)
        capital_flow = "capital_entering_crypto"
        institutional = min(75.0, institutional + 8.0)
        reasons.append("USDT dominance implies risk-on capital flow.")

    btc_d_trend = regime.dominance.btc_dominance_trend
    if btc_d_trend is not None:
        if btc_d_trend.value == "rising":
            secondary.append(MarketNarrative.BITCOIN_SEASON)
            capital_flow = "rotation_into_bitcoin"
            reasons.append("BTC.D rising — capital rotating into Bitcoin.")
        elif btc_d_trend.value == "falling":
            secondary.append(MarketNarrative.ALTCOIN_SEASON)
            capital_flow = "rotation_into_altcoins"
            reasons.append("BTC.D falling — capital rotating into alts.")

    if regime.funding.status is FundingStatus.VERY_POSITIVE:
        secondary.append(MarketNarrative.LONG_SQUEEZE)
        driver = "leveraged_buying"
        reversal = min(0.75, reversal + 0.08)
        reasons.append("Very positive funding — crowded longs / squeeze risk.")
    elif regime.funding.status is FundingStatus.VERY_NEGATIVE:
        secondary.append(MarketNarrative.SHORT_SQUEEZE)
        driver = "leveraged_selling"
        reversal = min(0.75, reversal + 0.08)
        reasons.append("Very negative funding — crowded shorts / squeeze risk.")

    if regime.open_interest.relation is OiPriceRelation.PRICE_UP_OI_UP:
        secondary.append(MarketNarrative.FUTURES_DRIVEN_RALLY)
        institutional = min(80.0, institutional + 10.0)
        reasons.append("Price + OI rising — leveraged trend participation.")
    elif regime.open_interest.relation is OiPriceRelation.PRICE_DOWN_OI_UP:
        secondary.append(MarketNarrative.FUTURES_DRIVEN_SELLOFF)
        reasons.append("Price down + OI rising — short build / liquidation risk.")
    elif regime.open_interest.relation is OiPriceRelation.PRICE_UP_OI_DOWN:
        secondary.append(MarketNarrative.SPOT_DRIVEN_RALLY)
        institutional = min(85.0, institutional + 5.0)
        reasons.append("Price up + OI down — spot-led rally (higher confidence).")

    if regime.fear_greed.band is FearGreedBand.EXTREME_FEAR:
        secondary.append(MarketNarrative.RETAIL_PANIC)
        secondary.append(MarketNarrative.LIQUIDITY_COLLECTION)
        reasons.append("Extreme fear — panic / potential capitulation context.")
    elif regime.fear_greed.band is FearGreedBand.EXTREME_GREED:
        secondary.append(MarketNarrative.RETAIL_FOMO)
        secondary.append(MarketNarrative.TREND_EXHAUSTION)
        reversal = min(0.8, reversal + 0.1)
        reasons.append("Extreme greed — euphoria / distribution risk.")

    liq = regime.liquidations
    if liq.available and liq.liquidity_score is not None and liq.liquidity_score < 40:
        health = "weak"
        reasons.append("Liquidity score weak — market health reduced.")
    elif abs(bias.score) >= 55 and regime.global_score >= 60:
        health = "healthy"
    elif abs(bias.score) >= 80:
        health = "excellent" if bias.score > 0 else "critical"

    # Prefer squeeze / season narratives as primary when strongly evidenced.
    for candidate in (
        MarketNarrative.SHORT_SQUEEZE,
        MarketNarrative.LONG_SQUEEZE,
        MarketNarrative.LIQUIDATION_CASCADE,
        MarketNarrative.ALTCOIN_SEASON,
        MarketNarrative.BITCOIN_SEASON,
    ):
        if candidate in secondary:
            secondary = [n for n in secondary if n is not candidate]
            if primary is MarketNarrative.TREND_CONTINUATION and candidate in (
                MarketNarrative.SHORT_SQUEEZE,
                MarketNarrative.LONG_SQUEEZE,
            ):
                primary = candidate
            break

    secondary = [n for n in secondary if n is not primary][:3]
    confidence = clamp_score(45.0 + abs(bias.score) * 0.35 + (10.0 if regime.funding.available else 0.0))

    return NarrativeSnapshot(
        primary=primary,
        secondary=tuple(secondary),
        primary_driver=driver,
        institutional_participation=clamp_score(institutional),
        capital_flow=capital_flow,
        market_health=health,
        continuation_probability=round(continuation, 4),
        reversal_probability=round(reversal, 4),
        confidence=confidence,
        reasons=tuple(reasons),
    )
