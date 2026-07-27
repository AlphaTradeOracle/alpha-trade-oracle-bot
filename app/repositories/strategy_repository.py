"""Datenzugriff fuer Strategien und ihre versionierten Gewichtungen."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models.strategy import Strategy, StrategyVersion
from app.strategies.weights import StrategyWeights

DEFAULT_STRATEGY_NAME = "default"


class StrategyRepository:
    """Strategien und Strategieversionen.

    Gewichte werden nie in einer bestehenden Version ueberschrieben. Eine
    Aenderung erzeugt immer eine neue Version — nur so bleibt nachvollziehbar,
    mit welchen Parametern ein historisches Signal entstanden ist.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_strategy(self, name: str = DEFAULT_STRATEGY_NAME) -> Strategy | None:
        result = await self._session.execute(select(Strategy).where(Strategy.name == name))
        return result.scalar_one_or_none()

    async def get_or_create_strategy(
        self, name: str = DEFAULT_STRATEGY_NAME, *, description: str | None = None
    ) -> Strategy:
        existing = await self.get_strategy(name)
        if existing is not None:
            return existing
        strategy = Strategy(name=name, description=description)
        self._session.add(strategy)
        await self._session.flush()
        return strategy

    async def get_active_version(self, name: str = DEFAULT_STRATEGY_NAME) -> StrategyVersion | None:
        result = await self._session.execute(
            select(StrategyVersion)
            .join(Strategy)
            .where(Strategy.name == name, StrategyVersion.is_active.is_(True))
            .order_by(StrategyVersion.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_version(self, version_id: int) -> StrategyVersion | None:
        return await self._session.get(StrategyVersion, version_id)

    async def list_versions(self, name: str = DEFAULT_STRATEGY_NAME) -> list[StrategyVersion]:
        result = await self._session.execute(
            select(StrategyVersion)
            .join(Strategy)
            .where(Strategy.name == name)
            .order_by(StrategyVersion.version.desc())
        )
        return list(result.scalars())

    async def create_version(
        self,
        weights: StrategyWeights,
        *,
        name: str = DEFAULT_STRATEGY_NAME,
        min_score: float = 65.0,
        min_risk_reward_ratio: float = 2.0,
        atr_multiplier: float = 1.5,
        activate: bool = False,
        notes: str | None = None,
    ) -> StrategyVersion:
        """Neue Strategieversion anlegen.

        ``activate=False`` ist der Standard: eine neue Gewichtung darf im
        Produktivbetrieb nicht ungeprueft aktiv werden.
        """
        strategy = await self.get_or_create_strategy(name)

        highest = await self._session.execute(
            select(func.max(StrategyVersion.version)).where(
                StrategyVersion.strategy_id == strategy.id
            )
        )
        next_version = int(highest.scalar_one() or 0) + 1

        version = StrategyVersion(
            strategy_id=strategy.id,
            version=next_version,
            is_active=False,
            notes=notes,
            min_score=min_score,
            min_risk_reward_ratio=min_risk_reward_ratio,
            atr_multiplier=atr_multiplier,
            **weights.to_db_columns(),
        )
        self._session.add(version)
        await self._session.flush()

        if activate:
            await self.activate_version(version.id)
        return version

    async def activate_version(self, version_id: int) -> StrategyVersion | None:
        """Genau eine Version aktiv setzen; alle anderen derselben Strategie deaktivieren."""
        version = await self._session.get(StrategyVersion, version_id)
        if version is None:
            return None

        siblings = await self._session.execute(
            select(StrategyVersion).where(
                StrategyVersion.strategy_id == version.strategy_id,
                StrategyVersion.is_active.is_(True),
            )
        )
        for other in siblings.scalars():
            other.is_active = False

        version.is_active = True
        version.activated_at = utc_now()
        return version

    async def load_weights(
        self, name: str = DEFAULT_STRATEGY_NAME
    ) -> tuple[StrategyWeights | None, int | None]:
        """Aktive Gewichtung laden. Rueckgabe: ``(weights, version_id)``."""
        version = await self.get_active_version(name)
        if version is None:
            return None, None
        return StrategyWeights.from_db_columns(version), version.id
