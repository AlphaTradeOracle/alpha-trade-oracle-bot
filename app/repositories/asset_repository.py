"""Datenzugriff fuer Instrumente, Kerzen und Indikator-Snapshots."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import nulls_first, nulls_last, select, update
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

    async def upsert_universe_entry(
        self,
        *,
        symbol: str,
        base_asset: str,
        quote_asset: str,
        exchange: str,
        coingecko_id: str,
        market_cap_rank: int,
        market_cap_usd: Decimal | None,
        price_precision: int = 2,
        quantity_precision: int = 6,
        is_active: bool = True,
    ) -> Asset:
        """Asset fuer das Scan-Universe anlegen oder Ranking-Felder aktualisieren."""
        now = utc_now()
        existing = await self.get_by_symbol(symbol)
        if existing is not None:
            existing.base_asset = base_asset
            existing.quote_asset = quote_asset
            existing.exchange = exchange
            existing.coingecko_id = coingecko_id
            existing.market_cap_rank = market_cap_rank
            existing.market_cap_usd = market_cap_usd
            existing.in_universe = True
            existing.is_active = is_active
            existing.price_precision = price_precision
            existing.quantity_precision = quantity_precision
            existing.last_ranked_at = now
            return existing

        asset = Asset(
            symbol=symbol.upper(),
            base_asset=base_asset,
            quote_asset=quote_asset,
            exchange=exchange,
            price_precision=price_precision,
            quantity_precision=quantity_precision,
            is_active=is_active,
            coingecko_id=coingecko_id,
            market_cap_rank=market_cap_rank,
            market_cap_usd=market_cap_usd,
            in_universe=True,
            last_ranked_at=now,
        )
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def deactivate_stale_universe(self, active_symbols: set[str]) -> int:
        """``in_universe`` fuer Symbole ausserhalb der aktuellen Top-N zuruecksetzen."""
        normalized = {symbol.upper() for symbol in active_symbols}
        if not normalized:
            statement = (
                update(Asset)
                .where(Asset.in_universe.is_(True))
                .values(in_universe=False, market_cap_rank=None)
            )
        else:
            statement = (
                update(Asset)
                .where(Asset.in_universe.is_(True), Asset.symbol.notin_(normalized))
                .values(in_universe=False, market_cap_rank=None)
            )
        result = await self._session.execute(statement)
        return int(result.rowcount or 0)

    async def list_universe_batch(self, limit: int) -> list[Asset]:
        """Naechste Round-Robin-Batch aus dem aktiven Universe."""
        if limit <= 0:
            return []
        result = await self._session.execute(
            select(Asset)
            .where(Asset.in_universe.is_(True), Asset.is_active.is_(True))
            .order_by(nulls_first(Asset.last_scanned_at), Asset.market_cap_rank, Asset.symbol)
            .limit(limit)
        )
        return list(result.scalars())

    async def list_universe(self, *, limit: int | None = None) -> list[Asset]:
        """Aktives Universe nach Market-Cap-Rang."""
        statement = (
            select(Asset)
            .where(Asset.in_universe.is_(True), Asset.is_active.is_(True))
            .order_by(nulls_last(Asset.market_cap_rank), Asset.symbol)
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await self._session.execute(statement)
        return list(result.scalars())

    async def mark_scanned(self, symbol: str) -> None:
        """Scan-Zeitstempel fuer Round-Robin setzen."""
        await self._session.execute(
            update(Asset)
            .where(Asset.symbol == symbol.upper())
            .values(last_scanned_at=utc_now())
        )

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
