"""Coinbase Advanced Trade Marktdaten ueber oeffentliche REST-Endpunkte.

Nutzt ``/market/products`` und ``/market/products/{id}/candles`` ohne API-Key.
Coinbase-Produkt-IDs (``BTC-USD``) werden intern auf ``BTCUSD`` normalisiert.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import MarketDataError, SymbolNotFoundError
from app.core.http import RateLimiter, request_with_retry
from app.core.logging import get_logger
from app.core.time import ms_to_datetime, timeframe_to_timedelta
from app.market_data.types import Candle, CandleSeries, SymbolInfo

logger = get_logger(__name__)

#: Coinbase liefert maximal 350 Kerzen pro Candles-Aufruf.
MAX_CANDLES_PER_REQUEST = 350

#: Konservativ — oeffentliche Endpunkte sind gecacht (1s), Pagination braucht Puffer.
RATE_LIMIT_CALLS = 30
RATE_LIMIT_PERIOD_SECONDS = 60.0

MAX_CONCURRENT_REQUESTS = 4

#: Bevorzugte Quote-Waehrung je Base, wenn mehrere Spot-Paare existieren.
QUOTE_PRIORITY = {"USD": 0, "USDC": 1, "USDT": 2}

TIMEFRAME_TO_COINBASE: dict[str, str] = {
    "1m": "ONE_MINUTE",
    "5m": "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR",
    "2h": "TWO_HOUR",
    "4h": "FOUR_HOUR",
    "6h": "SIX_HOUR",
    "1d": "ONE_DAY",
}


class CoinbaseMarketDataProvider:
    """Implementierung von :class:`~app.market_data.base.MarketDataProvider`."""

    name = "coinbase"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._settings.coinbase_base_url.rstrip("/"),
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
            headers={"User-Agent": "alpha-trade-oracle-bot/0.1", "Accept": "application/json"},
        )
        self._rate_limiter = RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD_SECONDS)
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._symbol_cache: dict[str, SymbolInfo] = {}
        self._native_symbols: dict[str, str] = {}
        self._price_cache: dict[str, float] = {}
        self._symbol_cache_expires_at: datetime | None = None

    async def list_symbols(self, quote_asset: str | None = None) -> list[SymbolInfo]:
        symbols = await self._load_products()
        values = list(symbols.values())
        if quote_asset:
            wanted = quote_asset.upper()
            values = [info for info in values if info.quote_asset == wanted]
        return sorted(values, key=lambda info: info.symbol)

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        normalized = _to_app_symbol(symbol)
        symbols = await self._load_products()
        info = symbols.get(normalized)
        if info is None:
            raise SymbolNotFoundError(normalized)
        return info

    async def get_price(self, symbol: str) -> float:
        normalized = _to_app_symbol(symbol)
        await self._load_products()
        cached = self._price_cache.get(normalized)
        if cached is not None:
            return cached
        native = await self._native_symbol(symbol)
        payload = await self._get(f"/market/products/{native}", None)
        if not isinstance(payload, dict):
            raise MarketDataError(
                f"Unerwartete Preisantwort fuer {symbol}.", detail=str(payload)[:200]
            )
        product = payload.get("product") if "product" in payload else payload
        if not isinstance(product, dict) or "price" not in product:
            raise MarketDataError(
                f"Unerwartete Preisantwort fuer {symbol}.", detail=str(payload)[:200]
            )
        return float(product["price"])

    async def get_prices(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        wanted = {_to_app_symbol(symbol) for symbol in symbols}
        await self._load_products()
        return {
            app_symbol: self._price_cache[app_symbol]
            for app_symbol in wanted
            if app_symbol in self._price_cache
        }

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
        normalized = _to_app_symbol(symbol)
        interval = timeframe_to_timedelta(timeframe)
        granularity = TIMEFRAME_TO_COINBASE.get(timeframe)
        if granularity is None:
            raise MarketDataError(
                f"Timeframe {timeframe!r} wird von Coinbase nicht unterstuetzt.",
                detail=f"Unterstuetzt: {', '.join(sorted(TIMEFRAME_TO_COINBASE))}",
            )

        raw = await self._fetch_candles(
            normalized,
            granularity,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
            interval=interval,
        )
        candles = [self._parse_candle(item, interval) for item in raw]

        if not include_unclosed:
            candles = self._drop_unclosed(candles, interval)

        candles.sort(key=lambda candle: candle.open_time)
        if limit and len(candles) > limit:
            candles = candles[-limit:]

        missing, gaps = self._detect_gaps(candles, interval)
        if missing:
            logger.warning(
                "market_data_gaps_detected",
                symbol=normalized,
                timeframe=timeframe,
                missing_candles=missing,
                gap_count=len(gaps),
            )

        return CandleSeries(
            symbol=normalized,
            timeframe=timeframe,
            candles=candles,
            missing_candles=missing,
            gaps=gaps,
            source=self.name,
        )

    async def get_multi_timeframe_candles(
        self, symbol: str, timeframes: list[str], *, limit: int = 500
    ) -> dict[str, CandleSeries]:
        async def load(timeframe: str) -> tuple[str, CandleSeries | None]:
            try:
                return timeframe, await self.get_candles(symbol, timeframe, limit=limit)
            except MarketDataError as exc:
                logger.warning(
                    "market_data_timeframe_failed",
                    symbol=symbol,
                    timeframe=timeframe,
                    error=str(exc),
                )
                return timeframe, None

        results = await asyncio.gather(*(load(tf) for tf in timeframes))
        return {tf: series for tf, series in results if series is not None}

    async def health_check(self) -> bool:
        try:
            response = await self._request("GET", "/time")
            return response.status_code == 200
        except Exception as exc:
            logger.warning("coinbase_health_check_failed", error=str(exc))
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _load_products(self) -> dict[str, SymbolInfo]:
        now = datetime.now(tz=None).astimezone()
        if (
            self._symbol_cache
            and self._symbol_cache_expires_at
            and now < self._symbol_cache_expires_at
        ):
            return self._symbol_cache

        allowed_quotes = {
            quote.strip().upper()
            for quote in self._settings.coinbase_quote_assets.split(",")
            if quote.strip()
        }
        if not allowed_quotes:
            allowed_quotes = {"USD", "USDC", "USDT"}

        by_base: dict[str, tuple[int, SymbolInfo, str]] = {}
        price_cache: dict[str, float] = {}
        cursor: str | None = None

        while True:
            params: dict[str, Any] = {"limit": 250, "product_type": "SPOT"}
            if cursor:
                params["cursor"] = cursor
            payload = await self._get("/market/products", params)
            if not isinstance(payload, dict):
                raise MarketDataError("Unerwartete Antwort von /market/products.")

            for entry in payload.get("products", []):
                parsed = _parse_product(entry, allowed_quotes)
                if parsed is None:
                    continue
                app_symbol, info, native, quote = parsed
                rank = QUOTE_PRIORITY.get(quote, 99)
                existing = by_base.get(info.base_asset)
                if existing is None or rank < existing[0]:
                    by_base[info.base_asset] = (rank, info, native)
                price_raw = entry.get("price")
                if price_raw is not None:
                    try:
                        price_cache[app_symbol] = float(price_raw)
                    except (TypeError, ValueError):
                        pass

            pagination = payload.get("pagination") or {}
            if not pagination.get("has_next"):
                break
            cursor = str(pagination.get("next_cursor") or "").strip() or None
            if not cursor:
                break

        cache = {info.symbol: info for _, info, _ in by_base.values()}
        native_map = {info.symbol: native for _, info, native in by_base.values()}
        self._symbol_cache = cache
        self._native_symbols = native_map
        self._price_cache = price_cache
        self._symbol_cache_expires_at = now + timedelta(hours=1)
        logger.info("coinbase_products_loaded", symbol_count=len(cache))
        return cache

    async def _fetch_candles(
        self,
        symbol: str,
        granularity: str,
        *,
        limit: int,
        start_time: datetime | None,
        end_time: datetime | None,
        interval: timedelta,
    ) -> list[dict[str, Any]]:
        native = await self._native_symbol(symbol)
        collected: list[dict[str, Any]] = []
        remaining = max(1, limit)
        cursor_end = end_time or datetime.now().astimezone()
        hard_start = start_time
        max_pages = 25

        for _ in range(max_pages):
            batch_size = min(remaining, MAX_CANDLES_PER_REQUEST)
            window_start = cursor_end - interval * batch_size
            if hard_start is not None and window_start < hard_start:
                window_start = hard_start

            params = {
                "start": str(int(window_start.timestamp())),
                "end": str(int(cursor_end.timestamp())),
                "granularity": granularity,
            }
            batch = await self._get_candles_page(native, params, symbol)
            if not batch:
                break

            batch_sorted = sorted(batch, key=lambda row: int(row["start"]))
            collected = batch_sorted + collected
            remaining = limit - len(collected)
            if remaining <= 0 or len(batch_sorted) < batch_size:
                break

            earliest = int(batch_sorted[0]["start"])
            cursor_end = ms_to_datetime(earliest * 1000 - 1)
            if hard_start is not None and cursor_end <= hard_start:
                break

        unique: dict[int, dict[str, Any]] = {}
        for row in collected:
            unique[int(row["start"])] = row
        return [unique[key] for key in sorted(unique)]

    async def _get_candles_page(
        self, native: str, params: dict[str, Any], symbol: str
    ) -> list[dict[str, Any]]:
        payload = await self._get(f"/market/products/{native}/candles", params)
        if not isinstance(payload, dict):
            raise MarketDataError(
                f"Unerwartete Candles-Antwort fuer {symbol}.", detail=str(payload)[:200]
            )
        candles = payload.get("candles")
        if not isinstance(candles, list):
            raise MarketDataError(
                f"Unerwartete Candles-Antwort fuer {symbol}.", detail=str(payload)[:200]
            )
        return [item for item in candles if isinstance(item, dict)]

    async def _native_symbol(self, symbol: str) -> str:
        normalized = _to_app_symbol(symbol)
        await self._load_products()
        native = self._native_symbols.get(normalized)
        if native is None:
            raise SymbolNotFoundError(normalized)
        return native

    async def _get(self, path: str, params: dict[str, Any] | None) -> Any:
        response = await self._request("GET", path, params=params)
        body = _safe_json(response)
        if response.status_code >= 400:
            raise MarketDataError(
                f"Coinbase-Fehler HTTP {response.status_code} bei {path}.",
                detail=str(body)[:200],
            )
        return body

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None) -> httpx.Response:
        async with self._semaphore:
            await self._rate_limiter.acquire()
            return await request_with_retry(
                self._client,
                method,
                path,
                max_retries=self._settings.http_max_retries,
                params=params,
            )

    @staticmethod
    def _parse_candle(item: dict[str, Any], interval: timedelta) -> Candle:
        open_time = ms_to_datetime(int(item["start"]) * 1000)
        return Candle(
            open_time=open_time,
            close_time=open_time + interval,
            open=float(item["open"]),
            high=float(item["high"]),
            low=float(item["low"]),
            close=float(item["close"]),
            volume=float(item.get("volume") or 0.0),
            is_closed=True,
        )

    @staticmethod
    def _drop_unclosed(candles: list[Candle], interval: timedelta) -> list[Candle]:
        now = datetime.now(tz=None).astimezone()
        return [candle for candle in candles if candle.open_time + interval <= now]

    @staticmethod
    def _detect_gaps(candles: list[Candle], interval: timedelta) -> tuple[int, list[tuple[datetime, datetime]]]:
        if len(candles) < 2:
            return 0, []
        expected = interval.total_seconds()
        gaps: list[tuple[datetime, datetime]] = []
        missing = 0
        for prev, nxt in pairwise(candles):
            delta = (nxt.open_time - prev.open_time).total_seconds()
            if delta > expected * 1.5:
                missing += int(delta / expected) - 1
                gaps.append((prev.close_time, nxt.open_time))
        return missing, gaps


def _to_app_symbol(symbol: str) -> str:
    return symbol.upper().strip().replace("-", "").replace("_", "").replace("/", "")


def _parse_product(
    entry: dict[str, Any], allowed_quotes: set[str]
) -> tuple[str, SymbolInfo, str, str] | None:
    if str(entry.get("product_type", "")).upper() != "SPOT":
        return None
    if str(entry.get("status", "")).lower() != "online":
        return None
    if entry.get("trading_disabled") or entry.get("is_disabled") or entry.get("view_only"):
        return None

    native = str(entry.get("product_id") or "").strip()
    base = str(entry.get("base_currency_id") or entry.get("base_display_symbol") or "").upper()
    quote = str(entry.get("quote_currency_id") or entry.get("quote_display_symbol") or "").upper()
    if not native or not base or not quote or quote not in allowed_quotes:
        return None

    app_symbol = f"{base}{quote}"
    info = SymbolInfo(
        symbol=app_symbol,
        base_asset=base,
        quote_asset=quote,
        price_precision=_decimal_precision(entry.get("price_increment"), default=2),
        quantity_precision=_decimal_precision(entry.get("base_increment"), default=8),
        is_active=True,
    )
    return app_symbol, info, native, quote


def _decimal_precision(value: Any, *, default: int) -> int:
    if value is None:
        return default
    text = str(value).strip()
    if not text or "." not in text:
        return default
    fractional = text.split(".", 1)[1].rstrip("0")
    return max(len(fractional), 0) or default


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text
