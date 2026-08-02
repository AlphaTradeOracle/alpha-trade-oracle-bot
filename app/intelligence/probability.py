"""Probability / Expected Value Engine (KB Part 5)."""

from __future__ import annotations

from app.core.enums import SignalDirection
from app.intelligence.types import (
    InstitutionalContext,
    ProbabilitySnapshot,
    clamp_score,
)
from app.signals.types import SignalResult


def estimate_probabilities(
    result: SignalResult,
    ctx: InstitutionalContext,
) -> ProbabilitySnapshot:
    """Heuristic EV model from confluence + regime + narrative + history."""
    # Map trade score to base win probability (calibrated loosely).
    score = float(result.score)
    if result.direction.is_short:
        quality = 100.0 - score
    elif result.direction.is_long:
        quality = score
    else:
        quality = 50.0

    win_p = 0.35 + (clamp_score(quality) / 100.0) * 0.40  # 0.35..0.75
    reasons: list[str] = [f"Base win probability from setup quality {quality:.1f}."]

    if ctx.narrative is not None:
        win_p = 0.7 * win_p + 0.3 * ctx.narrative.continuation_probability
        reasons.append(
            f"Narrative continuation {ctx.narrative.continuation_probability:.2f} blended in."
        )
        if result.direction.is_long and ctx.narrative.primary.value.endswith("selloff"):
            win_p -= 0.06
            reasons.append("Narrative selloff conflicts with long.")
        if result.direction.is_short and ctx.narrative.primary.value.endswith("rally"):
            win_p -= 0.06
            reasons.append("Narrative rally conflicts with short.")

    if ctx.structure is not None:
        align = _structure_aligns(result.direction, ctx.structure.bos, ctx.structure.choch)
        if align > 0:
            win_p += 0.04
            reasons.append("Structure aligns with trade direction.")
        elif align < 0:
            win_p -= 0.05
            reasons.append("Structure conflicts with trade direction.")
        if not ctx.structure.volume_confirmed:
            win_p -= 0.03

    if ctx.historical is not None and ctx.historical.sample_size > 0:
        edge = ctx.historical.historical_edge
        win_p += max(-0.08, min(0.08, edge))
        reasons.append(f"Historical edge adjustment {edge:+.3f}.")

    if ctx.data_quality is not None:
        win_p += ctx.data_quality.confidence_adjustment / 200.0

    win_p = max(0.15, min(0.85, win_p))
    loss_p = 1.0 - win_p

    rr = result.risk.risk_reward_ratio if result.risk is not None else 1.5
    # EV in R-multiples: win_p * RR - loss_p * 1
    expected_value = win_p * rr - loss_p * 1.0

    continuation = ctx.narrative.continuation_probability if ctx.narrative else 0.5
    reversal = ctx.narrative.reversal_probability if ctx.narrative else 0.5
    breakout = 0.45 + (quality - 50.0) / 200.0
    fake = 1.0 - breakout
    if ctx.phase and ctx.phase.phase.value == "compression":
        breakout += 0.08
        fake -= 0.05
        reasons.append("Compression phase raises breakout probability.")
    if ctx.phase and ctx.phase.phase.value == "low_volatility":
        fake += 0.08
        reasons.append("Low volatility raises fake-breakout probability.")

    sweep = 0.40
    if ctx.structure and ctx.structure.liquidity_status == "thin":
        sweep += 0.1

    return ProbabilitySnapshot(
        win_probability=round(win_p, 4),
        loss_probability=round(loss_p, 4),
        expected_value=round(expected_value, 4),
        continuation_probability=round(continuation, 4),
        reversal_probability=round(reversal, 4),
        breakout_success=round(max(0.1, min(0.9, breakout)), 4),
        fake_breakout=round(max(0.1, min(0.9, fake)), 4),
        liquidity_sweep=round(max(0.1, min(0.9, sweep)), 4),
        reasons=tuple(reasons),
    )


def _structure_aligns(direction: SignalDirection, bos: str | None, choch: str | None) -> int:
    tags = {bos, choch}
    if direction.is_long:
        if "bullish" in tags:
            return 1
        if "bearish" in tags:
            return -1
    if direction.is_short:
        if "bearish" in tags:
            return 1
        if "bullish" in tags:
            return -1
    return 0
