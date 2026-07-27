"""Redis-Cache-Dekorator fuer Marktdaten.

Der Dekorator umschliesst einen beliebigen ``MarketDataProvider``. Faellt Redis
aus, wird transparent direkt am Provider vorbei gearbeitet — der Cache darf
niemals zum Single Point of Failure werden.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.time import ensure_utc
from app.market_data.base import MarketDataProvider
from app.market_data.types import Candle, CandleSeries, SymbolInfo

logger = get_logger(__name__)

CACHE_PREFIX = "market_data"


class CachedMarketDataProvider:
    """Cachet nur Abrufe ohne explizites Zeitfenster.

    Historische Abfragen mit ``start_time``/``end_time`` (Backtest) werden nicht
    gecacht: sie sind gross, einmalig und wuerden den Cache verdraengen.
    """

    def __init__(
        self,
        provider: MarketDataProvider,
        redis_client: Any,
        settings: Settings | None = None,
    ) -> None:
        self._provider = provider
        self._redis = redis_client
        self._settings = settings or get_settings()
        self._ttl = self._settings.market_data_cache_ttl_seconds

    @property
    def name(self) -> str:
        return self._provider.name

    async def list_symbols(self, quote_asset: str | None = None) -> list[SymbolInfo]:
        return await self._provider.list_symbols(quote_asset)

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return await self._provider.get_symbol_info(symbol)

    async def get_price(self, symbol: str) -> float:
        return await self._provider.get_price(symbol)

    async def get_prices(self, symbols: list[str]) -> dict[str, float]:
        return await self._provider.get_prices(symbols)

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 500,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        include_unclosed: bool = False,
    ) -> CandleSeries:
        cacheable = start_time is None and end_time is None and not include_unclosed
        key = f"{CACHE_PREFIX}:candles:{self.name}:{symbol.upper()}:{timeframe}:{limit}"

        if cacheable:
            cached = await self._read(key)
            if cached is not None:
                return cached

        series = await self._provider.get_candles(
            symbol,
            timeframe,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
            include_unclosed=include_unclosed,
        )

        if cacheable and not series.is_empty:
            await self._write(key, series)
        return series

    async def get_multi_timeframe_candles(
        self, symbol: str, timeframes: list[str], *, limit: int = 500
    ) -> dict[str, CandleSeries]:
        result: dict[str, CandleSeries] = {}
        for timeframe in timeframes:
            try:
                result[timeframe] = await self.get_candles(symbol, timeframe, limit=limit)
            except Exception as exc:
                logger.warning(
                    "cached_provider_timeframe_failed",
                    symbol=symbol,
                    timeframe=timeframe,
                    error=str(exc),
                )
        return result

    async def health_check(self) -> bool:
        return await self._provider.health_check()

    async def close(self) -> None:
        await self._provider.close()

    # --- Cache-Zugriff ----------------------------------------------------

    async def _read(self, key: str) -> CandleSeries | None:
        try:
            raw = await self._redis.get(key)
        except Exception as exc:
            logger.debug("market_data_cache_read_failed", key=key, error=str(exc))
            return None

        if not raw:
            return None
        try:
            return _deserialize(raw)
        except (ValueError, KeyError, TypeError) as exc:
            logger.debug("market_data_cache_corrupt", key=key, error=str(exc))
            return None

    async def _write(self, key: str, series: CandleSeries) -> None:
        try:
            await self._redis.set(key, _serialize(series), ex=self._ttl)
        except Exception as exc:
            logger.debug("market_data_cache_write_failed", key=key, error=str(exc))


def _serialize(series: CandleSeries) -> str:
    return json.dumps(
        {
            "symbol": series.symbol,
            "timeframe": series.timeframe,
            "missing_candles": series.missing_candles,
            "source": series.source,
            "candles": [
                {
                    "ot": candle.open_time.isoformat(),
                    "ct": candle.close_time.isoformat(),
                    "o": candle.open,
                    "h": candle.high,
                    "l": candle.low,
                    "c": candle.close,
                    "v": candle.volume,
                    "qv": candle.quote_volume,
                    "n": candle.trade_count,
                }
                for candle in series.candles
            ],
        },
        separators=(",", ":"),
    )


def _deserialize(raw: str) -> CandleSeries:
    payload = json.loads(raw)
    candles = [
        Candle(
            open_time=ensure_utc(datetime.fromisoformat(item["ot"])),
            close_time=ensure_utc(datetime.fromisoformat(item["ct"])),
            open=float(item["o"]),
            high=float(item["h"]),
            low=float(item["l"]),
            close=float(item["c"]),
            volume=float(item["v"]),
            quote_volume=float(item["qv"]) if item.get("qv") is not None else None,
            trade_count=int(item["n"]) if item.get("n") is not None else None,
        )
        for item in payload["candles"]
    ]
    return CandleSeries(
        symbol=payload["symbol"],
        timeframe=payload["timeframe"],
        candles=candles,
        missing_candles=int(payload.get("missing_candles", 0)),
        source=str(payload.get("source", "cache")),
    )
