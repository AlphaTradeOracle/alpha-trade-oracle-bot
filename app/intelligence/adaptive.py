"""Adaptive Intelligence & Performance Engine (KB Part 8) — bootstrap stub."""

from __future__ import annotations

from app.intelligence.types import AdaptiveSnapshot, InstitutionalContext, clamp_score
from app.market_regime.types import MarketBias


def evaluate_adaptive_performance(ctx: InstitutionalContext) -> AdaptiveSnapshot:
    """
    Bootstrap adaptive layer until live trade-journal stats are wired.

    Uses regime/phase health as a prior; does not invent win-rates.
    """
    notes: list[str] = [
        "Adaptive engine bootstrap — awaiting sufficient closed-trade sample.",
    ]
    performance = 55.0
    historical_conf = 50.0
    robustness = 50.0
    risk_mult = 1.0
    conf_adj = 0.0
    strategy = "trend_following"

    if ctx.phase is not None:
        phase = ctx.phase.phase.value
        if phase in ("trending_bullish", "trending_bearish", "expansion"):
            strategy = "trend_following"
            performance = 62.0
            notes.append(f"Phase {ctx.phase.phase.label} historically suits trend following.")
        elif phase == "range":
            strategy = "mean_reversion"
            performance = 58.0
            notes.append("Range phase — prefer mean reversion / fade extremes.")
        elif phase == "compression":
            strategy = "breakout"
            performance = 56.0
            notes.append("Compression — prefer breakout with confirmation.")
        elif phase in ("high_volatility", "capitulation"):
            strategy = "reduced_risk"
            risk_mult = 0.6
            conf_adj = -8.0
            performance = 45.0
            notes.append("High volatility / capitulation — reduce risk multiplier.")
        elif phase == "low_volatility":
            strategy = "wait"
            risk_mult = 0.7
            conf_adj = -5.0
            notes.append("Low volatility — avoid forcing entries.")

    bias = ctx.bias
    if bias is MarketBias.NEUTRAL:
        conf_adj -= 3.0
        notes.append("Neutral regime — slight confidence haircut.")
    elif bias in (MarketBias.STRONG_BULLISH, MarketBias.STRONG_BEARISH):
        historical_conf = 60.0
        robustness = 58.0

    if ctx.narrative is not None and ctx.narrative.market_health in ("weak", "critical"):
        risk_mult *= 0.75
        conf_adj -= 6.0
        notes.append("Market health weak/critical — cut risk.")

    return AdaptiveSnapshot(
        performance_score=clamp_score(performance),
        historical_confidence=clamp_score(historical_conf),
        recommended_strategy=strategy,
        recommended_risk_mult=round(max(0.25, min(1.25, risk_mult)), 3),
        confidence_adjustment=round(conf_adj, 2),
        robustness_score=clamp_score(robustness),
        notes=tuple(notes),
    )
