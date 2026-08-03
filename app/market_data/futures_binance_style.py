"""Binance-style USDT-M futures market data (Binance fapi / Aster fapi).

Public REST only — used for paper fill / TP / SL wick simulation.
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
from app.core.time import datetime_to_ms, ms_to_datetime, timeframe_to_timedelta
from app.market_data.leverage_coverage import normalize_base
from app.market_data.types import Candle, CandleSeries, SymbolInfo

logger = get_logger(__name__)

MAX_KLINES_PER_REQUEST = 1500
RATE_LIMIT_CALLS = 800
RATE_LIMIT_PERIOD_SECONDS = 60.0
MAX_CONCURRENT_REQUESTS = 6


class BinanceStyleFuturesProvider:
    """``MarketDataProvider`` for Binance-compatible perpetual REST APIs."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        name: str,
        base_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self._settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
            headers={"User-Agent": "alpha-trade-oracle-bot/perp", "Accept": "application/json"},
        )
        self._rate_limiter = RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD_SECONDS)
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._symbol_cache: dict[str, SymbolInfo] = {}
        self._base_to_symbol: dict[str, str] = {}
        self._symbol_cache_expires_at: datetime | None = None

    async def list_symbols(self, quote_asset: str | None = None) -> list[SymbolInfo]:
        symbols = await self._load_exchange_info()
        values = list(symbols.values())
        if quote_asset:
            wanted = quote_asset.upper()
            values = [info for info in values if info.quote_asset == wanted]
        return sorted(values, key=lambda info: info.symbol)

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        native = await self.resolve_native_symbol(symbol)
        symbols = await self._load_exchange_info()
        info = symbols.get(native)
        if info is None:
            raise SymbolNotFoundError(symbol)
        return info

    async def resolve_native_symbol(self, symbol: str) -> str:
        """Map desk ``BTCUSDT`` → venue perpetual symbol."""
        from app.market_data.leverage_coverage import base_alias_candidates

        await self._load_exchange_info()
        normalized = symbol.upper().strip().replace("-", "").replace("/", "")
        if normalized in self._symbol_cache:
            return normalized
        base = _base_from_symbol(normalized)
        for candidate in base_alias_candidates(base):
            mapped = self._base_to_symbol.get(candidate)
            if mapped:
                return mapped
        raise SymbolNotFoundError(symbol)

    async def supports_base(self, base: str) -> bool:
        from app.market_data.leverage_coverage import base_alias_candidates

        await self._load_exchange_info()
        return any(c in self._base_to_symbol for c in base_alias_candidates(base))

    async def get_price(self, symbol: str) -> float:
        native = await self.resolve_native_symbol(symbol)
        payload = await self._get("/fapi/v1/ticker/price", {"symbol": native})
        if not isinstance(payload, dict) or "price" not in payload:
            raise MarketDataError(
                f"Unexpected futures price for {native}.", detail=str(payload)[:200]
            )
        return float(payload["price"])

    async def get_prices(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        # Prefer mark/last book once, then map back to requested symbols.
        payload = await self._get("/fapi/v1/ticker/price", None)
        if not isinstance(payload, list):
            raise MarketDataError("Unexpected futures multi-price response.")
        by_native = {str(item["symbol"]).upper(): float(item["price"]) for item in payload}
        out: dict[str, float] = {}
        for symbol in symbols:
            key = symbol.upper().strip()
            try:
                native = await self.resolve_native_symbol(key)
            except SymbolNotFoundError:
                continue
            if native in by_native:
                out[key] = by_native[native]
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
        native = await self.resolve_native_symbol(symbol)
        interval = timeframe_to_timedelta(timeframe)
        raw = await self._fetch_klines(
            native, timeframe, limit=limit, start_time=start_time, end_time=end_time
        )
        candles = [_parse_kline(row) for row in raw]
        if not include_unclosed:
            candles = _drop_unclosed(candles, interval)
        candles.sort(key=lambda c: c.open_time)
        missing, gaps = _detect_gaps(candles, interval)
        return CandleSeries(
            symbol=symbol.upper().strip(),
            timeframe=timeframe,
            candles=candles,
            missing_candles=missing,
            gaps=gaps,
            source=self.name,
        )

    async def get_multi_timeframe_candles(
        self, symbol: str, timeframes: list[str], *, limit: int = 500
    ) -> dict[str, CandleSeries]:
        async def load(tf: str) -> tuple[str, CandleSeries | None]:
            try:
                return tf, await self.get_candles(symbol, tf, limit=limit)
            except MarketDataError as exc:
                logger.warning(
                    "futures_timeframe_failed",
                    venue=self.name,
                    symbol=symbol,
                    timeframe=tf,
                    error=str(exc),
                )
                return tf, None

        results = await asyncio.gather(*(load(tf) for tf in timeframes))
        return {tf: series for tf, series in results if series is not None}

    async def health_check(self) -> bool:
        try:
            response = await self._request("GET", "/fapi/v1/ping")
            return response.status_code == 200
        except Exception as exc:
            logger.warning("futures_health_failed", venue=self.name, error=str(exc))
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _load_exchange_info(self) -> dict[str, SymbolInfo]:
        now = datetime.now().astimezone()
        if (
            self._symbol_cache
            and self._symbol_cache_expires_at
            and now < self._symbol_cache_expires_at
        ):
            return self._symbol_cache

        payload = await self._get("/fapi/v1/exchangeInfo", None)
        if not isinstance(payload, dict) or "symbols" not in payload:
            raise MarketDataError(f"Unexpected exchangeInfo from {self.name}.")

        cache: dict[str, SymbolInfo] = {}
        base_map: dict[str, str] = {}
        for entry in payload["symbols"]:
            ctype = entry.get("contractType")
            if ctype is not None and str(ctype).upper() not in {"PERPETUAL", ""}:
                continue
            if entry.get("status") and str(entry.get("status")).upper() != "TRADING":
                continue
            quote = str(entry.get("quoteAsset") or "").upper()
            if quote and quote not in {"USDT", "USDC"}:
                continue
            symbol = str(entry.get("symbol") or "").upper()
            base = normalize_base(str(entry.get("baseAsset") or ""))
            if not symbol or not base:
                continue
            cache[symbol] = SymbolInfo(
                symbol=symbol,
                base_asset=base,
                quote_asset=quote or "USDT",
                is_active=True,
            )
            # Prefer USDT listing when both exist.
            prev = base_map.get(base)
            if prev is None or (symbol.endswith("USDT") and not prev.endswith("USDT")):
                base_map[base] = symbol

        self._symbol_cache = cache
        self._base_to_symbol = base_map
        self._symbol_cache_expires_at = now + timedelta(hours=1)
        logger.info(
            "futures_exchange_info_loaded",
            venue=self.name,
            symbols=len(cache),
            bases=len(base_map),
        )
        return cache

    async def _fetch_klines(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[list[Any]]:
        if start_time is None:
            return await self._fetch_klines_backwards(symbol, timeframe, limit, end_time)
        return await self._fetch_klines_forward(symbol, timeframe, start_time, end_time, limit)

    async def _fetch_klines_backwards(
        self, symbol: str, timeframe: str, limit: int, end_time: datetime | None
    ) -> list[list[Any]]:
        collected: list[list[Any]] = []
        cursor_end = end_time
        remaining = limit
        while remaining > 0:
            batch_size = min(remaining, MAX_KLINES_PER_REQUEST)
            params: dict[str, Any] = {
                "symbol": symbol,
                "interval": timeframe,
                "limit": batch_size,
            }
            if cursor_end is not None:
                params["endTime"] = datetime_to_ms(cursor_end)
            batch = await self._get_klines_page(params, symbol)
            if not batch:
                break
            collected = batch + collected
            remaining -= len(batch)
            if len(batch) < batch_size:
                break
            cursor_end = ms_to_datetime(int(batch[0][0]) - 1)
        return collected

    async def _fetch_klines_forward(
        self,
        symbol: str,
        timeframe: str,
        start_time: datetime,
        end_time: datetime | None,
        limit: int,
    ) -> list[list[Any]]:
        collected: list[list[Any]] = []
        cursor = start_time
        end_ms = datetime_to_ms(end_time) if end_time else None
        for _ in range(500):
            if len(collected) >= limit:
                break
            params: dict[str, Any] = {
                "symbol": symbol,
                "interval": timeframe,
                "limit": min(MAX_KLINES_PER_REQUEST, limit - len(collected)),
                "startTime": datetime_to_ms(cursor),
            }
            if end_ms is not None:
                params["endTime"] = end_ms
            batch = await self._get_klines_page(params, symbol)
            if not batch:
                break
            collected.extend(batch)
            if len(batch) < MAX_KLINES_PER_REQUEST:
                break
            cursor = ms_to_datetime(int(batch[-1][0]) + 1)
            if end_ms is not None and datetime_to_ms(cursor) >= end_ms:
                break
        return collected[:limit]

    async def _get_klines_page(self, params: dict[str, Any], symbol: str) -> list[list[Any]]:
        payload = await self._get("/fapi/v1/klines", params)
        if not isinstance(payload, list):
            raise MarketDataError(
                f"Unexpected klines for {symbol} on {self.name}.",
                detail=str(payload)[:200],
            )
        return payload

    async def _get(self, path: str, params: dict[str, Any] | None) -> Any:
        response = await self._request("GET", path, params=params)
        if response.status_code == 400:
            detail = _safe_json(response)
            if isinstance(detail, dict) and detail.get("code") in (-1121, -1122):
                raise SymbolNotFoundError(str((params or {}).get("symbol", "?")))
            raise MarketDataError(
                f"{self.name} rejected {path}.",
                detail=str(detail)[:200],
            )
        if response.status_code >= 400:
            raise MarketDataError(
                f"{self.name} HTTP {response.status_code} at {path}.",
                detail=response.text[:200],
            )
        return _safe_json(response)

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        await self._rate_limiter.acquire()
        async with self._semaphore:
            return await request_with_retry(
                self._client,
                method,
                path,
                max_retries=self._settings.http_max_retries,
                params=params,
            )


def _base_from_symbol(symbol: str) -> str:
    s = symbol.upper().strip().replace("-", "").replace("/", "")
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return normalize_base(s[: -len(quote)])
    return normalize_base(s)


def _parse_kline(row: list[Any]) -> Candle:
    try:
        return Candle(
            open_time=ms_to_datetime(int(row[0])),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            close_time=ms_to_datetime(int(row[6])),
            quote_volume=float(row[7]) if len(row) > 7 else None,
            trade_count=int(row[8]) if len(row) > 8 else None,
        )
    except (IndexError, TypeError, ValueError) as exc:
        raise MarketDataError(
            "Could not parse futures kline.",
            detail=f"row={str(row)[:120]}",
        ) from exc


def _drop_unclosed(candles: list[Candle], interval: timedelta) -> list[Candle]:
    now = ms_to_datetime(datetime_to_ms(datetime.now().astimezone()))
    return [c for c in candles if c.open_time + interval <= now]


def _detect_gaps(
    candles: list[Candle], interval: timedelta
) -> tuple[int, list[tuple[datetime, datetime]]]:
    if len(candles) < 2:
        return 0, []
    missing = 0
    gaps: list[tuple[datetime, datetime]] = []
    step = interval.total_seconds()
    for previous, current in pairwise(candles):
        delta = (current.open_time - previous.open_time).total_seconds()
        if delta > step * 1.5:
            gap_count = round(delta / step) - 1
            if gap_count > 0:
                missing += gap_count
                gaps.append((previous.open_time, current.open_time))
    return missing, gaps


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise MarketDataError(
            "Futures response was not JSON.",
            detail=response.text[:200],
        ) from exc
