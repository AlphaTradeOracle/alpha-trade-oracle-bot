"""KuCoin USDT-M futures market data for paper fills."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import MarketDataError, SymbolNotFoundError
from app.core.http import RateLimiter, request_with_retry
from app.core.logging import get_logger
from app.core.time import datetime_to_ms, ms_to_datetime, timeframe_to_timedelta
from app.market_data.futures_binance_style import (
    _base_from_symbol,
    _detect_gaps,
    _drop_unclosed,
    _parse_kline,
)
from app.market_data.leverage_coverage import normalize_base
from app.market_data.types import Candle, CandleSeries, SymbolInfo

logger = get_logger(__name__)

RATE_LIMIT_CALLS = 200
RATE_LIMIT_PERIOD_SECONDS = 60.0

#: KuCoin futures granularity is minutes.
_TF_TO_GRANULARITY = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
    "1w": 10080,
}


class KucoinFuturesProvider:
    name = "kucoin_futures"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        base_url: str = "https://api-futures.kucoin.com",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
            headers={"User-Agent": "alpha-trade-oracle-bot/perp", "Accept": "application/json"},
        )
        self._rate_limiter = RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD_SECONDS)
        self._semaphore = asyncio.Semaphore(4)
        self._base_to_symbol: dict[str, str] = {}
        self._symbol_cache: dict[str, SymbolInfo] = {}
        self._cache_expires_at: datetime | None = None

    async def list_symbols(self, quote_asset: str | None = None) -> list[SymbolInfo]:
        await self._load_contracts()
        values = list(self._symbol_cache.values())
        if quote_asset:
            wanted = quote_asset.upper()
            values = [v for v in values if v.quote_asset == wanted]
        return sorted(values, key=lambda i: i.symbol)

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        native = await self.resolve_native_symbol(symbol)
        info = self._symbol_cache.get(native)
        if info is None:
            raise SymbolNotFoundError(symbol)
        return info

    async def resolve_native_symbol(self, symbol: str) -> str:
        from app.market_data.leverage_coverage import base_alias_candidates

        await self._load_contracts()
        base = _base_from_symbol(symbol)
        for candidate in base_alias_candidates(base):
            mapped = self._base_to_symbol.get(candidate)
            if mapped:
                return mapped
        raise SymbolNotFoundError(symbol)

    async def supports_base(self, base: str) -> bool:
        from app.market_data.leverage_coverage import base_alias_candidates

        await self._load_contracts()
        return any(c in self._base_to_symbol for c in base_alias_candidates(base))

    async def get_price(self, symbol: str) -> float:
        native = await self.resolve_native_symbol(symbol)
        payload = await self._get("/api/v1/ticker", {"symbol": native})
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or data.get("price") is None:
            raise MarketDataError(
                f"Unexpected KuCoin futures ticker for {native}.",
                detail=str(payload)[:200],
            )
        return float(data["price"])

    async def get_prices(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for symbol in symbols:
            try:
                out[symbol.upper().strip()] = await self.get_price(symbol)
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
        native = await self.resolve_native_symbol(symbol)
        granularity = _TF_TO_GRANULARITY.get(timeframe)
        if granularity is None:
            raise MarketDataError(f"Unsupported KuCoin futures timeframe: {timeframe}")
        interval = timeframe_to_timedelta(timeframe)

        end = end_time or datetime.now().astimezone()
        if start_time is None:
            start = end - interval * max(limit, 1)
        else:
            start = start_time

        # KuCoin returns at most ~200 bars per call depending on range; page backwards.
        collected: list[list[Any]] = []
        cursor_end = end
        while len(collected) < limit:
            window_start = cursor_end - interval * min(200, limit)
            if start_time is not None and window_start < start:
                window_start = start
            params = {
                "symbol": native,
                "granularity": granularity,
                # KuCoin futures expects epoch milliseconds for from/to.
                "from": datetime_to_ms(window_start),
                "to": datetime_to_ms(cursor_end),
            }
            payload = await self._get("/api/v1/kline/query", params)
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                break
            # KuCoin futures kline: [time, open, high, low, close, volume] (sec)
            batch = [_kucoin_row_to_binance_shape(r, granularity) for r in rows]
            batch.sort(key=lambda r: int(r[0]))
            collected = batch + collected
            if len(batch) < 2:
                break
            cursor_end = ms_to_datetime(int(batch[0][0]) - 1)
            if cursor_end <= start:
                break

        # de-dupe + trim
        by_t = {int(r[0]): r for r in collected}
        ordered = [by_t[t] for t in sorted(by_t)][-limit:]
        candles = [_parse_kline(r) for r in ordered]
        if not include_unclosed:
            candles = _drop_unclosed(candles, interval)
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
        out: dict[str, CandleSeries] = {}
        for tf in timeframes:
            try:
                out[tf] = await self.get_candles(symbol, tf, limit=limit)
            except MarketDataError as exc:
                logger.warning(
                    "kucoin_futures_tf_failed",
                    symbol=symbol,
                    timeframe=tf,
                    error=str(exc),
                )
        return out

    async def health_check(self) -> bool:
        try:
            payload = await self._get("/api/v1/contracts/active", None)
            return isinstance(payload, dict) and payload.get("code") in (None, "200000", 200000, "200")
        except Exception:
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _load_contracts(self) -> None:
        now = datetime.now().astimezone()
        if self._base_to_symbol and self._cache_expires_at and now < self._cache_expires_at:
            return
        payload = await self._get("/api/v1/contracts/active", None)
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise MarketDataError("Unexpected KuCoin futures contracts response.")
        base_map: dict[str, str] = {}
        cache: dict[str, SymbolInfo] = {}
        for item in rows:
            native = str(item.get("symbol") or "").upper()
            base = normalize_base(str(item.get("baseCurrency") or ""))
            quote = str(item.get("quoteCurrency") or "USDT").upper()
            if not native or not base:
                continue
            if item.get("status") and str(item.get("status")).lower() not in {"open", "trading", ""}:
                # keep most; KuCoin uses status codes variably
                pass
            cache[native] = SymbolInfo(
                symbol=native,
                base_asset=base,
                quote_asset=quote,
                is_active=True,
            )
            prev = base_map.get(base)
            if prev is None or ("USDT" in native and "USDT" not in prev):
                base_map[base] = native
        self._symbol_cache = cache
        self._base_to_symbol = base_map
        self._cache_expires_at = now + timedelta(hours=1)
        logger.info("kucoin_futures_contracts_loaded", symbols=len(cache), bases=len(base_map))

    async def _get(self, path: str, params: dict[str, Any] | None) -> Any:
        await self._rate_limiter.acquire()
        async with self._semaphore:
            response = await request_with_retry(
                self._client,
                "GET",
                path,
                max_retries=self._settings.http_max_retries,
                params=params,
            )
        if response.status_code >= 400:
            raise MarketDataError(
                f"KuCoin futures HTTP {response.status_code} at {path}.",
                detail=response.text[:200],
            )
        try:
            return response.json()
        except ValueError as exc:
            raise MarketDataError("KuCoin futures non-JSON response.") from exc


def _kucoin_row_to_binance_shape(row: list[Any], granularity_min: int) -> list[Any]:
    """Convert KuCoin futures kline row into Binance-like shape.

    KuCoin futures returns ``[open_ms, open, high, low, close, volume, turnover]``.
    """
    open_ms = int(row[0])
    # Guard: older docs used seconds — normalize if needed.
    if open_ms < 10_000_000_000:
        open_ms *= 1000
    close_ms = open_ms + granularity_min * 60_000 - 1
    return [
        open_ms,
        row[1],
        row[2],
        row[3],
        row[4],
        row[5] if len(row) > 5 else 0,
        close_ms,
        row[6] if len(row) > 6 else 0,
        0,
    ]
