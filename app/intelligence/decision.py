"""Trade decision labels + confidence helpers (KB Part 5)."""

from __future__ import annotations

from app.core.enums import Confidence, SignalDirection
from app.intelligence.types import (
    InstitutionalContext,
    TradeDecisionLabel,
    clamp_score,
)
from app.signals.types import SignalResult


def confidence_to_pct(confidence: Confidence) -> float:
    return {"LOW": 40.0, "MEDIUM": 65.0, "HIGH": 85.0}.get(confidence.value, 50.0)


def compute_confidence_pct(result: SignalResult, ctx: InstitutionalContext) -> float:
    pct = confidence_to_pct(result.confidence)
    if ctx.data_quality is not None:
        pct += ctx.data_quality.confidence_adjustment
    if ctx.adaptive is not None:
        pct += ctx.adaptive.confidence_adjustment
    if ctx.narrative is not None and ctx.narrative.confidence < 50:
        pct -= 5.0
    if ctx.structure is not None and not ctx.structure.volume_confirmed:
        pct -= 4.0
    if ctx.phase is not None and ctx.phase.phase.value == "uncertain":
        pct -= 6.0
    # Multi-TF agreement boost
    agree = abs(result.multi_timeframe_agreement)
    if agree >= 0.6:
        pct += 5.0
    elif agree < 0.2:
        pct -= 5.0
    return clamp_score(pct)


def decide_label(
    result: SignalResult,
    *,
    confidence_pct: float,
    rejected: bool,
) -> TradeDecisionLabel:
    if rejected or not result.direction.is_actionable:
        return TradeDecisionLabel.NO_TRADE

    score = result.score
    if result.direction.is_long:
        if score >= 95 and confidence_pct >= 75:
            return TradeDecisionLabel.STRONG_BUY
        if score >= 85:
            return TradeDecisionLabel.BUY
        if score >= 70:
            return TradeDecisionLabel.WEAK_BUY
        return TradeDecisionLabel.WATCHLIST

    if result.direction.is_short:
        # Short quality uses low absolute scores.
        quality = 100.0 - score
        if quality >= 95 and confidence_pct >= 75:
            return TradeDecisionLabel.STRONG_SELL
        if quality >= 85:
            return TradeDecisionLabel.SELL
        if quality >= 70:
            return TradeDecisionLabel.WEAK_SELL
        return TradeDecisionLabel.WATCHLIST

    return TradeDecisionLabel.NO_TRADE


def build_natural_language(
    result: SignalResult,
    ctx: InstitutionalContext,
    *,
    decision: TradeDecisionLabel,
    confidence_pct: float,
    expected_value: float | None,
) -> str:
    parts: list[str] = [
        f"{result.symbol}: {decision.value.replace('_', ' ')} "
        f"(trade score {result.score:.1f}, confidence {confidence_pct:.0f}%).",
    ]
    if ctx.phase is not None:
        parts.append(
            f"Market phase {ctx.phase.phase.label} — {ctx.phase.expected_behaviour}"
        )
    if ctx.bias is not None:
        parts.append(f"Global bias {ctx.bias.label}.")
    if ctx.narrative is not None:
        parts.append(
            f"Primary narrative: {ctx.narrative.primary.label} "
            f"(driver={ctx.narrative.primary_driver}, health={ctx.narrative.market_health})."
        )
    if ctx.structure is not None:
        parts.append(
            f"Structure {ctx.structure.structure_label}; liquidity {ctx.structure.liquidity_status}."
        )
    if expected_value is not None:
        parts.append(f"Estimated EV {expected_value:+.3f}R.")
    if result.direction is SignalDirection.NO_TRADE and result.no_trade_reason:
        parts.append(f"Rejected: {result.no_trade_reason}.")
    return " ".join(parts)
