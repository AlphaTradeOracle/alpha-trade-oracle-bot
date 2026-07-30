"""Re-score persisted signals from stored raw_score components with current weights.

Updates ``signals.score``, ``signals.direction``, and component weights in-place.
Use before ``paper rebuild`` so gate filters reflect the new weight profile.
"""

from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.enums import ScoreCategory
from app.core.logging import configure_logging, get_logger
from app.database.session import session_scope
from app.models.signal import Signal, SignalScoreComponent
from app.signals.engine import SignalEngine
from app.signals.types import ScoreComponent
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

logger = get_logger(__name__)


def _effective_weights(settings) -> StrategyWeights:
    weights = DEFAULT_WEIGHTS
    return weights if settings.enable_sentiment else weights.without_sentiment()


def _recompute(signal: Signal, weights: StrategyWeights) -> tuple[float, str, list[tuple[SignalScoreComponent, float, float]]]:
    wmap = weights.as_dict()
    components: list[ScoreComponent] = []
    updates: list[tuple[SignalScoreComponent, float, float]] = []

    for comp in signal.score_components:
        cat = ScoreCategory(comp.category)
        weight = float(wmap.get(cat, 0.0))
        raw = float(comp.raw_score)
        components.append(
            ScoreComponent(category=cat, raw_score=raw, weight=weight)
        )
        updates.append((comp, weight, raw * weight))

    score = SignalEngine._weighted_score(components)
    agreement = SignalEngine._agreement_value(components)
    direction = SignalEngine._determine_direction(score, agreement)
    return score, direction.value, updates


async def run_rescore(*, dry_run: bool = False) -> dict:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    weights = _effective_weights(settings)

    stats = {
        "weights": weights.model_dump(),
        "total": 0,
        "updated": 0,
        "direction_changed": 0,
        "score_delta_avg": 0.0,
        "dry_run": dry_run,
    }
    score_deltas: list[float] = []

    async with session_scope() as session:
        result = await session.execute(
            select(Signal).options(selectinload(Signal.score_components)).order_by(Signal.id)
        )
        signals = list(result.scalars().all())
        stats["total"] = len(signals)

        for signal in signals:
            if not signal.score_components:
                continue
            old_score = float(signal.score)
            old_direction = signal.direction
            new_score, new_direction, updates = _recompute(signal, weights)

            if abs(new_score - old_score) < 1e-6 and new_direction == old_direction:
                continue

            stats["updated"] += 1
            score_deltas.append(new_score - old_score)
            if new_direction != old_direction:
                stats["direction_changed"] += 1

            if not dry_run:
                signal.score = new_score
                signal.direction = new_direction
                for comp, weight, weighted in updates:
                    comp.weight = weight
                    comp.weighted_score = weighted

        if score_deltas:
            stats["score_delta_avg"] = round(sum(score_deltas) / len(score_deltas), 3)

    logger.info("rescore_signals_done", **stats)
    return stats


async def main() -> int:
    dry_run = "--dry-run" in sys.argv
    stats = await run_rescore(dry_run=dry_run)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
