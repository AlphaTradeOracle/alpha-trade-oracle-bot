"""Route paper fill / TP / SL prices to perpetual venues.

Priority default: binance → kucoin → aster → hyperliquid.
No spot fallback — missing perp coverage raises / skips the symbol.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import MarketDataError, SymbolNotFoundError
from app.core.logging import get_logger
from app.market_data.futures_binance_style import (
    BinanceStyleFuturesProvider,
    _base_from_symbol,
)
from app.market_data.futures_hyperliquid import HyperliquidPerpProvider
from app.market_data.futures_kucoin import KucoinFuturesProvider
from app.market_data.leverage_coverage import normalize_base
from app.market_data.types import CandleSeries, SymbolInfo

logger = get_logger(__name__)

DEFAULT_VENUE_ORDER = ("binance", "kucoin", "aster", "hyperliquid")


class _PerpVenue(Protocol):
    name: str

    async def supports_base(self, base: str) -> bool: ...
    async def get_price(self, symbol: str) -> float: ...
    async def get_prices(self, symbols: list[str]) -> dict[str, float]: ...
    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 500,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        include_unclosed: bool = False,
    ) -> CandleSeries: ...
    async def get_multi_timeframe_candles(
        self, symbol: str, timeframes: list[str], *, limit: int = 500
    ) -> dict[str, CandleSeries]: ...
    async def list_symbols(self, quote_asset: str | None = None) -> list[SymbolInfo]: ...
    async def get_symbol_info(self, symbol: str) -> SymbolInfo: ...
    async def health_check(self) -> bool: ...
    async def close(self) -> None: ...


class PerpRouterProvider:
    """``MarketDataProvider`` that always reads perpetual prices/candles."""

    name = "perp_router"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        venues: dict[str, _PerpVenue] | None = None,
        venue_order: list[str] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._venue_order = venue_order or _parse_venue_order(
            getattr(self._settings, "paper_perp_venues", "") or ",".join(DEFAULT_VENUE_ORDER)
        )
        self._venues = venues or _build_default_venues(self._settings, self._venue_order)
        self._route_cache: dict[str, str] = {}

    @property
    def venue_names(self) -> list[str]:
        return list(self._venue_order)

    async def resolve_venue(self, symbol: str) -> _PerpVenue:
        base = _base_from_symbol(symbol)
        cached = self._route_cache.get(base)
        if cached and cached in self._venues:
            return self._venues[cached]

        for name in self._venue_order:
            venue = self._venues.get(name)
            if venue is None:
                continue
            try:
                if await venue.supports_base(base):
                    self._route_cache[base] = name
                    logger.debug("perp_route", symbol=symbol, base=base, venue=name)
                    return venue
            except Exception as exc:
                logger.warning("perp_venue_probe_failed", venue=name, base=base, error=str(exc))
                continue
        raise SymbolNotFoundError(symbol)

    async def list_symbols(self, quote_asset: str | None = None) -> list[SymbolInfo]:
        merged: dict[str, SymbolInfo] = {}
        for name in self._venue_order:
            venue = self._venues.get(name)
            if venue is None:
                continue
            try:
                for info in await venue.list_symbols(quote_asset=quote_asset):
                    desk_sym = f"{normalize_base(info.base_asset)}USDT"
                    merged.setdefault(desk_sym, SymbolInfo(
                        symbol=desk_sym,
                        base_asset=normalize_base(info.base_asset),
                        quote_asset="USDT",
                        is_active=info.is_active,
                    ))
            except Exception as exc:
                logger.warning("perp_list_symbols_failed", venue=name, error=str(exc))
        return sorted(merged.values(), key=lambda i: i.symbol)

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        venue = await self.resolve_venue(symbol)
        return await venue.get_symbol_info(symbol)

    async def get_price(self, symbol: str) -> float:
        venue = await self.resolve_venue(symbol)
        return await venue.get_price(symbol)

    async def get_prices(self, symbols: list[str]) -> dict[str, float]:
        """Batch by venue; never fall back to spot."""
        if not symbols:
            return {}
        by_venue: dict[str, list[str]] = {}
        for symbol in symbols:
            key = symbol.upper().strip()
            try:
                venue = await self.resolve_venue(key)
            except SymbolNotFoundError:
                logger.warning("perp_price_unroutable", symbol=key)
                continue
            by_venue.setdefault(venue.name, []).append(key)

        out: dict[str, float] = {}
        for venue_name, group in by_venue.items():
            venue = self._venues.get(venue_name) or next(
                (v for v in self._venues.values() if v.name == venue_name), None
            )
            if venue is None:
                continue
            try:
                out.update(await venue.get_prices(group))
            except Exception as exc:
                logger.warning(
                    "perp_batch_prices_failed",
                    venue=venue_name,
                    error=str(exc),
                    count=len(group),
                )
                for symbol in group:
                    try:
                        out[symbol] = await venue.get_price(symbol)
                    except Exception:
                        continue
        return out

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
        venue = await self.resolve_venue(symbol)
        series = await venue.get_candles(
            symbol,
            timeframe,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
            include_unclosed=include_unclosed,
        )
        # Preserve desk symbol; annotate source with venue.
        series.source = f"perp:{venue.name}"
        return series

    async def get_multi_timeframe_candles(
        self, symbol: str, timeframes: list[str], *, limit: int = 500
    ) -> dict[str, CandleSeries]:
        venue = await self.resolve_venue(symbol)
        return await venue.get_multi_timeframe_candles(symbol, timeframes, limit=limit)

    async def health_check(self) -> bool:
        checks = await asyncio.gather(
            *(v.health_check() for v in self._venues.values()),
            return_exceptions=True,
        )
        return any(c is True for c in checks)

    async def close(self) -> None:
        for venue in self._venues.values():
            try:
                await venue.close()
            except Exception as exc:
                logger.warning("perp_venue_close_failed", venue=venue.name, error=str(exc))


def _parse_venue_order(raw: str) -> list[str]:
    aliases = {
        "binance": "binance",
        "binance_futures": "binance",
        "kucoin": "kucoin",
        "kucoin_futures": "kucoin",
        "aster": "aster",
        "hyperliquid": "hyperliquid",
        "hl": "hyperliquid",
    }
    out: list[str] = []
    for part in raw.split(","):
        key = aliases.get(part.strip().lower())
        if key and key not in out:
            out.append(key)
    return out or list(DEFAULT_VENUE_ORDER)


def _build_default_venues(settings: Settings, order: list[str]) -> dict[str, _PerpVenue]:
    venues: dict[str, Any] = {}
    if "binance" in order:
        venues["binance"] = BinanceStyleFuturesProvider(
            settings,
            name="binance_futures",
            base_url=getattr(settings, "binance_futures_base_url", "https://fapi.binance.com"),
        )
    if "kucoin" in order:
        venues["kucoin"] = KucoinFuturesProvider(
            settings,
            base_url=getattr(settings, "kucoin_futures_base_url", "https://api-futures.kucoin.com"),
        )
    if "aster" in order:
        venues["aster"] = BinanceStyleFuturesProvider(
            settings,
            name="aster_futures",
            base_url=getattr(settings, "aster_futures_base_url", "https://fapi.asterdex.com"),
        )
    if "hyperliquid" in order:
        venues["hyperliquid"] = HyperliquidPerpProvider(
            settings,
            base_url=getattr(settings, "hyperliquid_base_url", "https://api.hyperliquid.xyz"),
        )
    return venues
