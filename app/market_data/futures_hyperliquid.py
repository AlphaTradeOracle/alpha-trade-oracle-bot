"""Hyperliquid perpetual market data for paper fills."""

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
)
from app.market_data.leverage_coverage import normalize_base
from app.market_data.types import Candle, CandleSeries, SymbolInfo

logger = get_logger(__name__)

RATE_LIMIT_CALLS = 60
RATE_LIMIT_PERIOD_SECONDS = 60.0

_SUPPORTED_TF = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w"}


class HyperliquidPerpProvider:
    name = "hyperliquid"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        base_url: str = "https://api.hyperliquid.xyz",
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
        self._coins: set[str] = set()
        self._cache_expires_at: datetime | None = None

    async def list_symbols(self, quote_asset: str | None = None) -> list[SymbolInfo]:
        await self._load_meta()
        return [
            SymbolInfo(symbol=f"{c}USDT", base_asset=c, quote_asset="USDT", is_active=True)
            for c in sorted(self._coins)
        ]

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        coin = await self.resolve_native_symbol(symbol)
        return SymbolInfo(symbol=f"{coin}USDT", base_asset=coin, quote_asset="USDT", is_active=True)

    async def resolve_native_symbol(self, symbol: str) -> str:
        """Return Hyperliquid coin name for a desk symbol."""
        await self._load_meta()
        base = _base_from_symbol(symbol)
        if base in self._coins:
            return base
        # kPEPE / 1000PEPE style aliases
        for candidate in (f"k{base}", f"K{base}", f"1000{base}"):
            if candidate in self._coins:
                return candidate
        if base.startswith("1000") and base[4:] in self._coins:
            return base[4:]
        raise SymbolNotFoundError(symbol)

    async def supports_base(self, base: str) -> bool:
        try:
            await self.resolve_native_symbol(f"{normalize_base(base)}USDT")
            return True
        except SymbolNotFoundError:
            return False

    async def get_price(self, symbol: str) -> float:
        coin = await self.resolve_native_symbol(symbol)
        mids = await self._all_mids()
        if coin not in mids:
            raise SymbolNotFoundError(symbol)
        return float(mids[coin])

    async def get_prices(self, symbols: list[str]) -> dict[str, float]:
        mids = await self._all_mids()
        out: dict[str, float] = {}
        for symbol in symbols:
            try:
                coin = await self.resolve_native_symbol(symbol)
            except SymbolNotFoundError:
                continue
            if coin in mids:
                out[symbol.upper().strip()] = float(mids[coin])
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
        if timeframe not in _SUPPORTED_TF:
            raise MarketDataError(f"Unsupported Hyperliquid timeframe: {timeframe}")
        coin = await self.resolve_native_symbol(symbol)
        interval = timeframe_to_timedelta(timeframe)
        end = end_time or datetime.now().astimezone()
        start = start_time or (end - interval * max(limit, 1))
        payload = await self._post(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": timeframe,
                    "startTime": datetime_to_ms(start),
                    "endTime": datetime_to_ms(end),
                },
            }
        )
        if not isinstance(payload, list):
            raise MarketDataError(
                f"Unexpected HL candles for {coin}.",
                detail=str(payload)[:200],
            )
        candles = [_hl_candle(row) for row in payload]
        candles.sort(key=lambda c: c.open_time)
        if len(candles) > limit:
            candles = candles[-limit:]
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
                    "hyperliquid_tf_failed",
                    symbol=symbol,
                    timeframe=tf,
                    error=str(exc),
                )
        return out

    async def health_check(self) -> bool:
        try:
            await self._load_meta()
            return bool(self._coins)
        except Exception:
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _load_meta(self) -> None:
        now = datetime.now().astimezone()
        if self._coins and self._cache_expires_at and now < self._cache_expires_at:
            return
        payload = await self._post({"type": "meta"})
        universe = payload.get("universe") if isinstance(payload, dict) else None
        coins: set[str] = set()
        if isinstance(universe, list):
            for item in universe:
                if item.get("isDelisted"):
                    continue
                name = str(item.get("name") or "").upper()
                if name:
                    coins.add(normalize_base(name))
        self._coins = coins
        self._cache_expires_at = now + timedelta(hours=1)
        logger.info("hyperliquid_meta_loaded", coins=len(coins))

    async def _all_mids(self) -> dict[str, str]:
        payload = await self._post({"type": "allMids"})
        if not isinstance(payload, dict):
            raise MarketDataError("Unexpected Hyperliquid allMids response.")
        return {str(k).upper(): str(v) for k, v in payload.items()}

    async def _post(self, body: dict[str, Any]) -> Any:
        await self._rate_limiter.acquire()
        async with self._semaphore:
            response = await request_with_retry(
                self._client,
                "POST",
                "/info",
                max_retries=self._settings.http_max_retries,
                json=body,
            )
        if response.status_code >= 400:
            raise MarketDataError(
                f"Hyperliquid HTTP {response.status_code}.",
                detail=response.text[:200],
            )
        try:
            return response.json()
        except ValueError as exc:
            raise MarketDataError("Hyperliquid non-JSON response.") from exc


def _hl_candle(row: dict[str, Any]) -> Candle:
    open_ms = int(row["t"])
    close_ms = int(row.get("T") or open_ms)
    return Candle(
        open_time=ms_to_datetime(open_ms),
        close_time=ms_to_datetime(close_ms),
        open=float(row["o"]),
        high=float(row["h"]),
        low=float(row["l"]),
        close=float(row["c"]),
        volume=float(row.get("v") or 0),
    )
