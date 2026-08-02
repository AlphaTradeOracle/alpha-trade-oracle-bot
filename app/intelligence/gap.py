"""Decision Gap Analysis & Trade Optimization (KB Part 9)."""

from __future__ import annotations

from app.core.enums import SignalDirection
from app.intelligence.types import (
    GapAnalysisSnapshot,
    InstitutionalContext,
    clamp_score,
)
from app.signals.types import SignalResult


def analyze_decision_gap(
    result: SignalResult,
    ctx: InstitutionalContext,
    *,
    min_trade_score: float,
    min_confidence_pct: float,
) -> GapAnalysisSnapshot:
    missing: list[str] = []
    blocking: list[str] = []
    negative: list[str] = []
    positive: list[str] = []
    recommendations: list[str] = []
    what_if: list[str] = []

    quality = result.score if result.direction.is_long else (
        100.0 - result.score if result.direction.is_short else 50.0
    )
    max_score = 100.0
    gap = max(0.0, max_score - quality)

    if ctx.market_regime is None or not ctx.market_regime.available:
        missing.append("Market Regime Confirmation")
        blocking.append("Market regime unavailable")
    else:
        bias = ctx.market_regime.bias
        if result.direction.is_long and bias.value in ("bearish", "strong_bearish"):
            negative.append(f"BTC/global bias {bias.label} against long")
            if bias.value == "strong_bearish":
                blocking.append("Strong bearish global regime")
        elif result.direction.is_short and bias.value in ("bullish", "strong_bullish"):
            negative.append(f"BTC/global bias {bias.label} against short")
            if bias.value == "strong_bullish":
                blocking.append("Strong bullish global regime")
        else:
            positive.append(f"Global bias {bias.label} compatible")

        if not ctx.market_regime.funding.available:
            missing.append("Funding Confirmation")
        elif ctx.market_regime.funding.status.value.startswith("very_"):
            negative.append("Extreme funding")
            what_if.append("If funding normalizes → confidence +8, gap −5")
        else:
            positive.append("Funding not extreme")

        if not ctx.market_regime.open_interest.available:
            missing.append("Open Interest Confirmation")

    if ctx.structure is None or ctx.structure.structure_score < 50:
        missing.append("Higher Timeframe Structure Confirmation")
        recommendations.append("Wait for higher-timeframe structure confirmation.")
    else:
        positive.append(f"Structure score {ctx.structure.structure_score:.0f}")
        if not ctx.structure.volume_confirmed:
            missing.append("Volume Confirmation")
            recommendations.append("Wait for volume confirmation.")
            what_if.append("If volume expands → trade score +4, confidence +6")
        if ctx.structure.liquidity_status in ("thin", "unknown"):
            missing.append("Liquidity Confirmation")
            negative.append("Liquidity unclear or thin")

    if ctx.narrative is None or ctx.narrative.confidence < 50:
        missing.append("Narrative Confirmation")
        negative.append("Narrative uncertain")
    else:
        positive.append(f"Primary narrative: {ctx.narrative.primary.label}")

    if ctx.data_quality is not None:
        if ctx.data_quality.trade_restricted:
            blocking.append("Data quality gate failed")
        for item in ctx.data_quality.unavailable:
            if item in ("funding", "open_interest", "fear_greed", "dominance"):
                missing.append(f"{item.replace('_', ' ').title()} Confirmation")

    conf = _confidence_pct(result, ctx)
    if result.direction.is_long and quality < min_trade_score:
        blocking.append(f"Trade score {quality:.1f} < {min_trade_score:.1f}")
        recommendations.append("Wait for stronger confluence before execution.")
    if conf < min_confidence_pct:
        blocking.append(f"Confidence {conf:.1f}% < {min_confidence_pct:.1f}%")
        recommendations.append("Raise confirmation bar or reduce size.")

    if result.risk is not None and result.risk.risk_reward_ratio < 1.5:
        negative.append(f"Risk/Reward {result.risk.risk_reward_ratio:.2f} modest")
        recommendations.append("Optimize entry zone or stop for better R:R.")

    if not result.direction.is_actionable:
        blocking.append("Engine decision is NO_TRADE")
        if result.no_trade_reason:
            blocking.append(result.no_trade_reason)

    if not recommendations:
        if blocking:
            recommendations.append("Cancel setup until blocking factors clear.")
        elif gap < 15:
            recommendations.append("Execute with standard size — gap small.")
        else:
            recommendations.append("Watchlist — wait for missing confirmations.")

    what_if.extend(
        [
            "If BTC confirms direction → confidence +10",
            "If liquidity sweep completes → trade score +5",
        ]
    )

    improvement = clamp_score(100.0 - gap - 5.0 * len(blocking))
    return GapAnalysisSnapshot(
        gap_score=clamp_score(gap),
        improvement_score=improvement,
        missing_confirmations=tuple(dict.fromkeys(missing)),
        blocking_factors=tuple(dict.fromkeys(blocking)),
        negative_factors=tuple(dict.fromkeys(negative)),
        positive_factors=tuple(dict.fromkeys(positive)),
        recommendations=tuple(dict.fromkeys(recommendations)),
        what_if=tuple(what_if[:5]),
    )


def _confidence_pct(result: SignalResult, ctx: InstitutionalContext) -> float:
    base = {"LOW": 40.0, "MEDIUM": 65.0, "HIGH": 85.0}.get(result.confidence.value, 50.0)
    if ctx.data_quality is not None:
        base += ctx.data_quality.confidence_adjustment
    if ctx.adaptive is not None:
        base += ctx.adaptive.confidence_adjustment
    return clamp_score(base)
