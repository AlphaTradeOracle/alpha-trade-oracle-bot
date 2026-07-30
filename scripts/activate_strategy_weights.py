"""Aktive Strategieversion auf ``DEFAULT_WEIGHTS`` setzen.

Legt bei abweichenden Gewichten eine neue Version an und aktiviert sie.
Idempotent: gleiche Gewichte wie aktiv → kein neuer Eintrag.
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.database.session import session_scope
from app.repositories.strategy_repository import DEFAULT_STRATEGY_NAME, StrategyRepository
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

logger = get_logger(__name__)

NOTES = (
    "Paper forward test (2026-07-30): MTF -5pp, Trend +1.5pp — "
    "sim variant reduce_multi_timeframe"
)


async def run_activate(*, notes: str = NOTES, force: bool = False) -> int:
    """Neue Version aktivieren, falls noetig. Gibt die Versionsnummer zurueck."""
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)

    target = DEFAULT_WEIGHTS
    async with session_scope() as session:
        repo = StrategyRepository(session)
        active = await repo.get_active_version(DEFAULT_STRATEGY_NAME)

        if active is not None and not force:
            current = StrategyWeights.from_db_columns(active)
            if current.model_dump() == target.model_dump():
                logger.info(
                    "strategy_weights_already_active",
                    version=active.version,
                    weights=target.as_dict(),
                )
                print(f"Bereits aktiv: default v{active.version}")
                return active.version

        version = await repo.create_version(
            target,
            name=DEFAULT_STRATEGY_NAME,
            min_score=settings.signal_min_score,
            min_risk_reward_ratio=settings.min_risk_reward_ratio,
            atr_multiplier=settings.atr_multiplier,
            notes=notes,
            activate=True,
        )
        logger.info(
            "strategy_weights_activated",
            version=version.version,
            weights=target.as_dict(),
        )
        print(f"Aktiviert: default v{version.version}")
        for key, value in target.model_dump().items():
            print(f"  {key}: {value:.4f}")
        return version.version


if __name__ == "__main__":
    asyncio.run(run_activate())
