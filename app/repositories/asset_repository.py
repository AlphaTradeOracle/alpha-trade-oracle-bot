"""Datenzugriff fuer Instrumente, Kerzen und Indikator-Snapshots."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.database.dialects import insert_ignore_duplicates
from app.indicators.engine import IndicatorSet
from app.market_data.types import CandleSeries, SymbolInfo
from app.models.market import Asset, IndicatorSnapshot, MarketCandle


class AssetRepository:
    """Instrumente und ihre Zeitreihen."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_symbol(self, symbol: str) -> Asset | None:
        result = await self._session.execute(select(Asset).where(Asset.symbol == symbol.upper()))
        return result.scalar_one_or_none()

    async def get_or_create(self, info: SymbolInfo, *, exchange: str = "binance") -> Asset:
        """Instrument holen oder anlegen. Idempotent ueber den UNIQUE-Index."""
        existing = await self.get_by_symbol(info.symbol)
        if existing is not None:
            # Praezision kann sich bei Boersen aendern; still nachziehen.
            if existing.price_precision != info.price_precision:
                existing.price_precision = info.price_precision
            return existing

        asset = Asset(
            symbol=info.symbol.upper(),
            base_asset=info.base_asset,
            quote_asset=info.quote_asset,
            exchange=exchange,
            price_precision=info.price_precision,
            quantity_precision=info.quantity_precision,
            is_active=info.is_active,
        )
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def get_symbols_by_ids(self, asset_ids: list[int]) -> dict[int, str]:
        """Symbole zu mehreren IDs in einer Abfrage auflösen."""
        if not asset_ids:
            return {}
        result = await self._session.execute(
            select(Asset.id, Asset.symbol).where(Asset.id.in_(set(asset_ids)))
        )
        return {row[0]: row[1] for row in result.all()}

    async def list_active(self) -> list[Asset]:
        result = await self._session.execute(
            select(Asset).where(Asset.is_active.is_(True)).order_by(Asset.symbol)
        )
        return list(result.scalars())

    async def upsert_candles(self, asset_id: int, series: CandleSeries) -> int:
        """Kerzen idempotent schreiben.

        ``ON CONFLICT DO NOTHING`` auf ``(asset_id, timeframe, open_time)`` macht
        wiederholte Importe unschaedlich — wichtig, weil Scans sich zeitlich
        ueberlappen koennen.
        """
        if series.is_empty:
            return 0

        rows = [
            {
                "asset_id": asset_id,
                "timeframe": series.timeframe,
                "open_time": candle.open_time,
                "close_time": candle.close_time,
                "open": Decimal(str(candle.open)),
                "high": Decimal(str(candle.high)),
                "low": Decimal(str(candle.low)),
                "close": Decimal(str(candle.close)),
                "volume": Decimal(str(candle.volume)),
                "quote_volume": (
                    Decimal(str(candle.quote_volume)) if candle.quote_volume is not None else None
                ),
                "trade_count": candle.trade_count,
                "is_closed": candle.is_closed,
            }
            for candle in series.candles
        ]

        statement = insert_ignore_duplicates(
            self._session,
            MarketCandle,
            rows,
            index_elements=["asset_id", "timeframe", "open_time"],
        )
        result = await self._session.execute(statement)
        # CursorResult.rowcount ist je nach Dialekt optional typisiert.
        return int(getattr(result, "rowcount", 0) or 0)

    async def save_indicator_snapshot(self, asset_id: int, indicators: IndicatorSet) -> None:
        """Indikator-Snapshot idempotent je Kerze schreiben."""
        payload = indicators.to_snapshot_dict()
        values = {
            "asset_id": asset_id,
            "timeframe": indicators.timeframe,
            "captured_at": utc_now(),
            "candle_open_time": indicators.candle_open_time,
            **{
                key: (Decimal(str(value)) if isinstance(value, float) else value)
                for key, value in payload.items()
            },
        }

        statement = insert_ignore_duplicates(
            self._session,
            IndicatorSnapshot,
            values,
            index_elements=["asset_id", "timeframe", "candle_open_time"],
        )
        await self._session.execute(statement)

    async def latest_snapshot(self, asset_id: int, timeframe: str) -> IndicatorSnapshot | None:
        result = await self._session.execute(
            select(IndicatorSnapshot)
            .where(
                IndicatorSnapshot.asset_id == asset_id,
                IndicatorSnapshot.timeframe == timeframe,
            )
            .order_by(IndicatorSnapshot.candle_open_time.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
