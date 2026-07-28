"""KuCoin-Marktdaten ueber die oeffentliche Spot-REST-API.

Es werden ausschliesslich oeffentliche Endpunkte genutzt
(``/api/v1/symbols``, ``/api/v1/market/candles``, ``/api/v1/market/orderbook/level1``,
``/api/v1/market/allTickers``). Ein API-Key ist fuer reine Marktanalysen nicht
erforderlich.

Intern normalisieren wir KuCoin-Symbole (``BTC-USDT``) auf die anwendungsweite
Schreibweise ``BTCUSDT``, damit Signal-Engine und Datenbank einheitlich bleiben.
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

#: KuCoin liefert maximal 1500 Kerzen pro Candles-Aufruf.
MAX_CANDLES_PER_REQUEST = 1500

#: Konservativ unter dem oeffentlichen KuCoin-Limit.
RATE_LIMIT_CALLS = 300
RATE_LIMIT_PERIOD_SECONDS = 30.0

MAX_CONCURRENT_REQUESTS = 6

#: Abbildung unserer Timeframes auf KuCoin-``type``-Werte.
TIMEFRAME_TO_KUCOIN: dict[str, str] = {
    "1m": "1min",
    "3m": "3min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1hour",
    "2h": "2hour",
    "4h": "4hour",
    "6h": "6hour",
    "8h": "8hour",
    "12h": "12hour",
    "1d": "1day",
    "1w": "1week",
}


class KucoinMarketDataProvider:
    """Implementierung von :class:`~app.market_data.base.MarketDataProvider`."""

    name = "kucoin"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._settings.kucoin_base_url,
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
            headers={"User-Agent": "alpha-trade-oracle-bot/0.1"},
        )
        self._rate_limiter = RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD_SECONDS)
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._symbol_cache: dict[str, SymbolInfo] = {}
        self._symbol_cache_expires_at: datetime | None = None
        #: Mapping ``BTCUSDT`` -> ``BTC-USDT`` fuer API-Aufrufe.
        self._native_symbols: dict[str, str] = {}

    # --- oeffentliche API -------------------------------------------------

    async def list_symbols(self, quote_asset: str | None = None) -> list[SymbolInfo]:
        symbols = await self._load_exchange_info()
        values = list(symbols.values())
        if quote_asset:
            wanted = quote_asset.upper()
            values = [info for info in values if info.quote_asset == wanted]
        return sorted(values, key=lambda info: info.symbol)

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        normalized = _to_app_symbol(symbol)
        symbols = await self._load_exchange_info()
        info = symbols.get(normalized)
        if info is None:
            raise SymbolNotFoundError(normalized)
        return info

    async def get_price(self, symbol: str) -> float:
        native = await self._native_symbol(symbol)
        payload = await self._get_data(
            "/api/v1/market/orderbook/level1", {"symbol": native}
        )
        if not isinstance(payload, dict) or "price" not in payload:
            raise MarketDataError(
                f"Unerwartete Preisantwort fuer {symbol}.", detail=str(payload)[:200]
            )
        return float(payload["price"])

    async def get_prices(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        wanted = {_to_app_symbol(symbol) for symbol in symbols}
        payload = await self._get_data("/api/v1/market/allTickers", None)
        if not isinstance(payload, dict) or "ticker" not in payload:
            raise MarketDataError("Unerwartete Antwort beim Abruf mehrerer Kurse.")

        prices: dict[str, float] = {}
        for item in payload["ticker"]:
            native = str(item.get("symbol", ""))
            app_symbol = _to_app_symbol(native)
            if app_symbol not in wanted:
                continue
            last = item.get("last")
            if last is None:
                continue
            prices[app_symbol] = float(last)
        return prices

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
        kucoin_type = TIMEFRAME_TO_KUCOIN.get(timeframe)
        if kucoin_type is None:
            raise MarketDataError(
                f"Timeframe {timeframe!r} wird von KuCoin nicht unterstuetzt.",
                detail=f"Unterstuetzt: {', '.join(sorted(TIMEFRAME_TO_KUCOIN))}",
            )

        raw = await self._fetch_candles(
            normalized,
            kucoin_type,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
            interval=interval,
        )
        candles = [self._parse_candle(row, interval) for row in raw]

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
            payload = await self._get_data("/api/v1/timestamp", None)
            return payload is not None
        except Exception as exc:  # noqa: BLE001 - Healthcheck darf nie hart fehlschlagen
            logger.warning("kucoin_health_check_failed", error=str(exc))
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # --- interne Helfer ---------------------------------------------------

    async def _native_symbol(self, symbol: str) -> str:
        normalized = _to_app_symbol(symbol)
        await self._load_exchange_info()
        native = self._native_symbols.get(normalized)
        if native is None:
            raise SymbolNotFoundError(normalized)
        return native

    async def _load_exchange_info(self) -> dict[str, SymbolInfo]:
        now = datetime.now().astimezone()
        if (
            self._symbol_cache
            and self._symbol_cache_expires_at
            and now < self._symbol_cache_expires_at
        ):
            return self._symbol_cache

        payload = await self._get_data("/api/v1/symbols", None)
        if not isinstance(payload, list):
            raise MarketDataError("Unerwartete Antwort von /api/v1/symbols.")

        cache: dict[str, SymbolInfo] = {}
        native_map: dict[str, str] = {}
        for entry in payload:
            native = str(entry.get("symbol", "")).upper()
            if not native:
                continue
            app_symbol = _to_app_symbol(native)
            cache[app_symbol] = SymbolInfo(
                symbol=app_symbol,
                base_asset=str(entry.get("baseCurrency", "")),
                quote_asset=str(entry.get("quoteCurrency", "")),
                price_precision=_decimal_precision(entry.get("priceIncrement"), default=2),
                quantity_precision=_decimal_precision(entry.get("baseIncrement"), default=6),
                is_active=bool(entry.get("enableTrading", False)),
            )
            native_map[app_symbol] = native

        self._symbol_cache = cache
        self._native_symbols = native_map
        self._symbol_cache_expires_at = now + timedelta(hours=1)
        logger.info("kucoin_exchange_info_loaded", symbol_count=len(cache))
        return cache

    async def _fetch_candles(
        self,
        symbol: str,
        kucoin_type: str,
        *,
        limit: int,
        start_time: datetime | None,
        end_time: datetime | None,
        interval: timedelta,
    ) -> list[list[Any]]:
        native = await self._native_symbol(symbol)
        collected: list[list[Any]] = []
        remaining = max(1, limit)
        cursor_end = end_time or datetime.now().astimezone()
        max_pages = 20

        for _ in range(max_pages):
            batch_size = min(remaining, MAX_CANDLES_PER_REQUEST)
            # Ohne startAt liefert KuCoin oft nur einen kurzen Ausschnitt; deshalb
            # berechnen wir ein Fenster rueckwaerts von cursor_end.
            window_start = start_time
            if window_start is None:
                window_start = cursor_end - interval * batch_size

            params: dict[str, Any] = {
                "symbol": native,
                "type": kucoin_type,
                "startAt": int(window_start.timestamp()),
                "endAt": int(cursor_end.timestamp()),
            }
            batch = await self._get_candles_page(params, symbol)
            if not batch:
                break

            # KuCoin liefert neueste zuerst; wir speichern aufsteigend.
            batch_sorted = sorted(batch, key=lambda row: int(float(row[0])))
            collected = batch_sorted + collected
            remaining = limit - len(collected)
            if remaining <= 0 or len(batch_sorted) < batch_size:
                break
            cursor_end = ms_to_datetime(int(float(batch_sorted[0][0])) * 1000 - 1)
            if start_time is not None and cursor_end <= start_time:
                break

        # Duplikate entfernen, falls Seiten ueberlappen.
        unique: dict[int, list[Any]] = {}
        for row in collected:
            unique[int(float(row[0]))] = row
        return [unique[key] for key in sorted(unique)]

    async def _get_candles_page(self, params: dict[str, Any], symbol: str) -> list[list[Any]]:
        payload = await self._get_data("/api/v1/market/candles", params)
        if not isinstance(payload, list):
            raise MarketDataError(
                f"Unerwartete Candles-Antwort fuer {symbol}.", detail=str(payload)[:200]
            )
        return payload

    async def _get_data(self, path: str, params: dict[str, Any] | None) -> Any:
        response = await self._request("GET", path, params=params)
        body = _safe_json(response)

        if response.status_code >= 400:
            raise MarketDataError(
                f"KuCoin-Fehler HTTP {response.status_code} bei {path}.",
                detail=str(body)[:200],
            )

        if not isinstance(body, dict):
            raise MarketDataError(
                f"Unerwartete KuCoin-Antwort bei {path}.", detail=str(body)[:200]
            )

        code = str(body.get("code", ""))
        if code != "200000":
            message = str(body.get("msg", body))
            if "symbol" in message.lower() or code in {"400100", "900001"}:
                raise SymbolNotFoundError(str((params or {}).get("symbol", "?")))
            raise MarketDataError(
                f"KuCoin hat die Anfrage abgelehnt ({code}) fuer {path}.",
                detail=message[:200],
            )
        return body.get("data")

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
    def _parse_candle(row: list[Any], interval: timedelta) -> Candle:
        """KuCoin-Candles: ``[time, open, close, high, low, volume, turnover]``."""
        try:
            open_time = ms_to_datetime(int(float(row[0])) * 1000)
            return Candle(
                open_time=open_time,
                open=float(row[1]),
                close=float(row[2]),
                high=float(row[3]),
                low=float(row[4]),
                volume=float(row[5]),
                close_time=open_time + interval,
                quote_volume=float(row[6]) if len(row) > 6 else None,
                is_closed=True,
            )
        except (IndexError, TypeError, ValueError) as exc:
            raise MarketDataError(
                "Kerzendaten von KuCoin konnten nicht gelesen werden.",
                detail=f"row={str(row)[:120]}",
            ) from exc

    @staticmethod
    def _drop_unclosed(candles: list[Candle], interval: timedelta) -> list[Candle]:
        now = ms_to_datetime(datetime_to_ms(datetime.now().astimezone()))
        return [candle for candle in candles if candle.open_time + interval <= now]

    @staticmethod
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


def _to_app_symbol(symbol: str) -> str:
    """``BTC-USDT`` und ``btcusdt`` auf ``BTCUSDT`` normalisieren."""
    return symbol.upper().strip().replace("-", "").replace("_", "").replace("/", "")


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise MarketDataError(
            "Antwort von KuCoin war kein gueltiges JSON.", detail=response.text[:200]
        ) from exc


def _decimal_precision(raw: object, *, default: int) -> int:
    text = str(raw or "")
    if "." not in text:
        return 0 if text.isdigit() else default
    decimals = text.rstrip("0").split(".")[1]
    return len(decimals) if decimals else 0
