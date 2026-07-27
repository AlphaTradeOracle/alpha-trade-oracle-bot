"""Binance-Marktdaten ueber die oeffentliche REST-API.

Es werden ausschliesslich oeffentliche Endpunkte genutzt (``/api/v3/klines``,
``/api/v3/ticker/price``, ``/api/v3/exchangeInfo``). Ein API-Key ist fuer reine
Marktanalysen daher nicht erforderlich.
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
from app.market_data.types import Candle, CandleSeries, SymbolInfo

logger = get_logger(__name__)

#: Binance erlaubt maximal 1000 Kerzen pro Klines-Aufruf.
MAX_KLINES_PER_REQUEST = 1000

#: Konservativ unter dem Binance-Limit von 1200 Gewichtseinheiten pro Minute.
RATE_LIMIT_CALLS = 900
RATE_LIMIT_PERIOD_SECONDS = 60.0

#: Nebenlaeufige Requests begrenzen, damit ein Multi-Timeframe-Abruf das Limit nicht sprengt.
MAX_CONCURRENT_REQUESTS = 6


class BinanceMarketDataProvider:
    """Implementierung von :class:`~app.market_data.base.MarketDataProvider`."""

    name = "binance"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._settings.binance_base_url,
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
            headers={"User-Agent": "alpha-trade-oracle-bot/0.1"},
        )
        self._rate_limiter = RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD_SECONDS)
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._symbol_cache: dict[str, SymbolInfo] = {}
        self._symbol_cache_expires_at: datetime | None = None

    # --- oeffentliche API -------------------------------------------------

    async def list_symbols(self, quote_asset: str | None = None) -> list[SymbolInfo]:
        symbols = await self._load_exchange_info()
        values = list(symbols.values())
        if quote_asset:
            wanted = quote_asset.upper()
            values = [info for info in values if info.quote_asset == wanted]
        return sorted(values, key=lambda info: info.symbol)

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        normalized = symbol.upper().strip()
        symbols = await self._load_exchange_info()
        info = symbols.get(normalized)
        if info is None:
            raise SymbolNotFoundError(normalized)
        return info

    async def get_price(self, symbol: str) -> float:
        normalized = symbol.upper().strip()
        payload = await self._get("/api/v3/ticker/price", {"symbol": normalized})
        if not isinstance(payload, dict) or "price" not in payload:
            raise MarketDataError(
                f"Unerwartete Preisantwort fuer {normalized}.", detail=str(payload)[:200]
            )
        return float(payload["price"])

    async def get_prices(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        normalized = [s.upper().strip() for s in symbols]
        # Binance erwartet die Symbolliste als JSON-Array im Query-String.
        joined = "[" + ",".join(f'"{s}"' for s in normalized) + "]"
        payload = await self._get("/api/v3/ticker/price", {"symbols": joined})
        if not isinstance(payload, list):
            raise MarketDataError("Unerwartete Antwort beim Abruf mehrerer Kurse.")
        return {str(item["symbol"]): float(item["price"]) for item in payload}

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
        normalized = symbol.upper().strip()
        interval = timeframe_to_timedelta(timeframe)  # validiert den Timeframe

        raw = await self._fetch_klines(
            normalized, timeframe, limit=limit, start_time=start_time, end_time=end_time
        )
        candles = [self._parse_kline(row) for row in raw]

        if not include_unclosed:
            candles = self._drop_unclosed(candles, interval)

        candles.sort(key=lambda candle: candle.open_time)
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
        """Alle Timeframes nebenlaeufig laden; einzelne Fehler brechen nicht alles ab."""

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
            response = await self._request("GET", "/api/v3/ping")
            return response.status_code == 200
        except Exception as exc:
            logger.warning("binance_health_check_failed", error=str(exc))
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # --- interne Helfer ---------------------------------------------------

    async def _load_exchange_info(self) -> dict[str, SymbolInfo]:
        """Symbolliste laden und eine Stunde cachen — sie aendert sich selten."""
        now = datetime.now(tz=None).astimezone()
        if (
            self._symbol_cache
            and self._symbol_cache_expires_at
            and now < self._symbol_cache_expires_at
        ):
            return self._symbol_cache

        payload = await self._get("/api/v3/exchangeInfo", None)
        if not isinstance(payload, dict) or "symbols" not in payload:
            raise MarketDataError("Unerwartete Antwort von /api/v3/exchangeInfo.")

        cache: dict[str, SymbolInfo] = {}
        for entry in payload["symbols"]:
            symbol = str(entry.get("symbol", "")).upper()
            if not symbol:
                continue
            cache[symbol] = SymbolInfo(
                symbol=symbol,
                base_asset=str(entry.get("baseAsset", "")),
                quote_asset=str(entry.get("quoteAsset", "")),
                price_precision=_tick_precision(entry, "PRICE_FILTER", "tickSize", default=2),
                quantity_precision=_tick_precision(entry, "LOT_SIZE", "stepSize", default=6),
                is_active=str(entry.get("status", "")).upper() == "TRADING",
            )

        self._symbol_cache = cache
        self._symbol_cache_expires_at = now + timedelta(hours=1)
        logger.info("binance_exchange_info_loaded", symbol_count=len(cache))
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
        """Klines laden und ueber mehrere Seiten hinweg zusammenfuegen."""
        if start_time is None:
            # Ohne Zeitfenster: nur die letzten ``limit`` Kerzen, ggf. mehrseitig.
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
            # Eine Millisekunde vor der ersten geladenen Kerze weitersuchen.
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
        # Sicherheitsgrenze gegen Endlosschleifen bei unerwarteten Antworten.
        max_pages = 500

        for _ in range(max_pages):
            params: dict[str, Any] = {
                "symbol": symbol,
                "interval": timeframe,
                "limit": MAX_KLINES_PER_REQUEST,
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

        return collected

    async def _get_klines_page(self, params: dict[str, Any], symbol: str) -> list[list[Any]]:
        payload = await self._get("/api/v3/klines", params)
        if not isinstance(payload, list):
            raise MarketDataError(
                f"Unerwartete Klines-Antwort fuer {symbol}.", detail=str(payload)[:200]
            )
        return payload

    async def _get(self, path: str, params: dict[str, Any] | None) -> Any:
        response = await self._request("GET", path, params=params)

        if response.status_code == 400:
            # Binance meldet unbekannte Symbole mit Code -1121.
            detail = _safe_json(response)
            if isinstance(detail, dict) and detail.get("code") == -1121:
                raise SymbolNotFoundError(str((params or {}).get("symbol", "?")))
            raise MarketDataError(
                f"Binance hat die Anfrage abgelehnt (HTTP 400) fuer {path}.",
                detail=str(detail)[:200],
            )
        if response.status_code >= 400:
            raise MarketDataError(
                f"Binance-Fehler HTTP {response.status_code} bei {path}.",
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

    @staticmethod
    def _parse_kline(row: list[Any]) -> Candle:
        """Binance-Klines-Format in eine Candle uebersetzen."""
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
                "Kerzendaten von Binance konnten nicht gelesen werden.",
                detail=f"row={str(row)[:120]}",
            ) from exc

    @staticmethod
    def _drop_unclosed(candles: list[Candle], interval: timedelta) -> list[Candle]:
        """Noch laufende Kerze entfernen.

        Signale duerfen nie auf einer unfertigen Kerze basieren — deren
        Schlusskurs kann sich noch beliebig aendern.
        """
        now = ms_to_datetime(datetime_to_ms(datetime.now().astimezone()))
        return [candle for candle in candles if candle.open_time + interval <= now]

    @staticmethod
    def _detect_gaps(
        candles: list[Candle], interval: timedelta
    ) -> tuple[int, list[tuple[datetime, datetime]]]:
        """Fehlende Kerzen anhand der erwarteten Taktung erkennen."""
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
            "Antwort von Binance war kein gueltiges JSON.", detail=response.text[:200]
        ) from exc


def _tick_precision(entry: dict[str, Any], filter_type: str, key: str, *, default: int) -> int:
    """Dezimalstellen aus einem Binance-Filter ableiten (z. B. tickSize 0.01 -> 2)."""
    for item in entry.get("filters", []):
        if item.get("filterType") != filter_type:
            continue
        raw = str(item.get(key, ""))
        if "." not in raw:
            return 0
        decimals = raw.rstrip("0").split(".")[1]
        return len(decimals)
    return default
