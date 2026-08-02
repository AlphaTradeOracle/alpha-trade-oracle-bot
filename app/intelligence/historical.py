"""Historical Pattern Recognition (KB Part 6) — bootstrap stub."""

from __future__ import annotations

from app.intelligence.types import HistoricalPatternSnapshot, InstitutionalContext


def match_historical_patterns(ctx: InstitutionalContext) -> HistoricalPatternSnapshot:
    """
    Placeholder until trade-journal similarity DB is populated.

    Returns transparent zero-sample stats so callers never invent edges.
    """
    pattern = "unknown"
    notes = [
        "Historical pattern DB not yet populated — no similarity edge applied.",
    ]
    if ctx.phase is not None:
        pattern = ctx.phase.phase.value
        notes.append(f"Current phase tagged as cluster seed: {ctx.phase.phase.label}.")
    if ctx.narrative is not None:
        notes.append(f"Narrative seed: {ctx.narrative.primary.label}.")

    return HistoricalPatternSnapshot(
        historical_confidence=40.0,
        similarity_score=0.0,
        historical_edge=0.0,
        pattern_class=pattern,
        sample_size=0,
        win_rate=None,
        notes=tuple(notes),
    )
