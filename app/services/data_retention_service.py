"""Daten-Hygiene: Top-N behalten, Historie begrenzen, History nachladen."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.time import utc_now
from app.market_data.base import MarketDataProvider
from app.models.market import Asset, IndicatorSnapshot, MarketCandle
from app.repositories.asset_repository import AssetRepository

logger = get_logger(__name__)


@dataclass
class PruneResult:
    deactivated_assets: int = 0
    deleted_candles_outside: int = 0
    deleted_candles_old: int = 0
    deleted_snapshots_outside: int = 0
    deleted_snapshots_old: int = 0

    def as_summary(self) -> dict[str, int]:
        return {
            "deactivated_assets": self.deactivated_assets,
            "deleted_candles_outside": self.deleted_candles_outside,
            "deleted_candles_old": self.deleted_candles_old,
            "deleted_snapshots_outside": self.deleted_snapshots_outside,
            "deleted_snapshots_old": self.deleted_snapshots_old,
        }


@dataclass
class BackfillResult:
    assets: int = 0
    timeframe_ok: int = 0
    timeframe_failed: int = 0
    candles_written: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)

    def as_summary(self) -> dict[str, int]:
        return {
            "assets": self.assets,
            "timeframe_ok": self.timeframe_ok,
            "timeframe_failed": self.timeframe_failed,
            "candles_written": self.candles_written,
            "failures": len(self.failures),
        }


class DataRetentionService:
    """Haelt nur Top-N Market-Cap-Assets und begrenzte Kerzenhistorie."""

    def __init__(
        self,
        providers: dict[str, MarketDataProvider],
        *,
        settings: Settings | None = None,
    ) -> None:
        self._providers = providers
        self._settings = settings or get_settings()

    async def prune(self, session: AsyncSession) -> PruneResult:
        """Assets ausserhalb Top-N deaktivieren und ueberzaehlige Kerzen loeschen."""
        out = PruneResult()
        max_rank = self._settings.universe_max_rank
        retention_days = max(1, self._settings.candle_retention_days)
        cutoff = utc_now() - timedelta(days=retention_days)

        if max_rank and max_rank > 0:
            deactivate = await session.execute(
                update(Asset)
                .where(
                    or_(
                        Asset.market_cap_rank.is_(None),
                        Asset.market_cap_rank > max_rank,
                    )
                )
                .values(in_universe=False, is_active=False)
            )
            out.deactivated_assets = int(deactivate.rowcount or 0)

            keep_ids = select(Asset.id).where(
                Asset.market_cap_rank.is_not(None),
                Asset.market_cap_rank <= max_rank,
            )
            candles_out = await session.execute(
                delete(MarketCandle).where(MarketCandle.asset_id.not_in(keep_ids))
            )
            out.deleted_candles_outside = int(candles_out.rowcount or 0)
            snaps_out = await session.execute(
                delete(IndicatorSnapshot).where(IndicatorSnapshot.asset_id.not_in(keep_ids))
            )
            out.deleted_snapshots_outside = int(snaps_out.rowcount or 0)

        candles_old = await session.execute(
            delete(MarketCandle).where(MarketCandle.open_time < cutoff)
        )
        out.deleted_candles_old = int(candles_old.rowcount or 0)
        snaps_old = await session.execute(
            delete(IndicatorSnapshot).where(IndicatorSnapshot.candle_open_time < cutoff)
        )
        out.deleted_snapshots_old = int(snaps_old.rowcount or 0)

        await session.flush()
        logger.info("data_pruned", **out.as_summary())
        return out

    async def backfill_history(
        self,
        session: AsyncSession,
        *,
        days: int | None = None,
        timeframes: list[str] | None = None,
        limit_assets: int | None = None,
    ) -> BackfillResult:
        """Historie fuer Top-N Assets nachladen (Default: Retention-Fenster)."""
        from app.database.session import session_scope

        targets, out, tfs, start, retention_days = await self._plan_backfill(
            session,
            days=days,
            timeframes=timeframes,
            limit_assets=limit_assets,
        )

        for asset_id, symbol, exchange, rank in targets:
            provider = self._providers.get(exchange) or next(
                iter(self._providers.values()), None
            )
            if provider is None:
                out.failures.append((symbol, "kein Provider"))
                continue

            async with session_scope() as asset_session:
                repo = AssetRepository(asset_session)
                for timeframe in tfs:
                    try:
                        limit = _history_limit(timeframe, retention_days)
                        series = await provider.get_candles(
                            symbol,
                            timeframe,
                            limit=limit,
                            start_time=start,
                            end_time=utc_now(),
                        )
                        written = await repo.upsert_candles(asset_id, series)
                        out.candles_written += written
                        out.timeframe_ok += 1
                    except Exception as exc:
                        out.timeframe_failed += 1
                        out.failures.append((f"{symbol}:{timeframe}", str(exc)))
                        logger.warning(
                            "history_backfill_failed",
                            symbol=symbol,
                            timeframe=timeframe,
                            error=str(exc),
                        )

            logger.info("history_backfill_asset_done", symbol=symbol, rank=rank)

        logger.info("history_backfill_completed", **out.as_summary())
        return out

    async def _plan_backfill(
        self,
        session: AsyncSession,
        *,
        days: int | None,
        timeframes: list[str] | None,
        limit_assets: int | None,
    ) -> tuple[
        list[tuple[int, str, str, int | None]],
        BackfillResult,
        list[str],
        datetime,
        int,
    ]:
        out = BackfillResult()
        retention_days = days or self._settings.candle_retention_days
        start = utc_now() - timedelta(days=max(1, retention_days))
        tfs = timeframes or list(self._settings.timeframes)
        max_rank = self._settings.universe_max_rank or None

        statement = (
            select(Asset)
            .where(Asset.in_universe.is_(True), Asset.is_active.is_(True))
            .order_by(Asset.market_cap_rank.asc().nulls_last(), Asset.symbol)
        )
        if max_rank and max_rank > 0:
            statement = statement.where(
                Asset.market_cap_rank.is_not(None),
                Asset.market_cap_rank <= max_rank,
            )
        if limit_assets and limit_assets > 0:
            statement = statement.limit(limit_assets)

        assets = list((await session.execute(statement)).scalars())
        out.assets = len(assets)
        targets = [
            (asset.id, asset.symbol, asset.exchange, asset.market_cap_rank)
            for asset in assets
        ]
        return targets, out, tfs, start, retention_days


def _history_limit(timeframe: str, days: int) -> int:
    per_day = {
        "1m": 1440,
        "5m": 288,
        "15m": 96,
        "30m": 48,
        "1h": 24,
        "2h": 12,
        "4h": 6,
        "6h": 4,
        "12h": 2,
        "1d": 1,
        "1w": 1,
    }.get(timeframe, 24)
    return min(100_000, max(500, days * per_day + 10))
