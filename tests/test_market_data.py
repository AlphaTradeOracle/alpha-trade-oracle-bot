"""Tests der Marktdaten-Schicht.

Alle Tests laufen ohne Netzwerk: die Binance-Antworten werden ueber einen
httpx-MockTransport nachgebildet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import MarketDataError, SymbolNotFoundError
from app.core.time import datetime_to_ms
from app.market_data.binance import BinanceMarketDataProvider
from app.market_data.types import Candle, CandleSeries, SymbolInfo

#: Ohne Wiederholungen laufen die Fehlerfaelle ohne Backoff-Wartezeit.
NO_RETRY_SETTINGS = Settings(http_max_retries=0)

BASE_TIME = datetime(2024, 1, 1, tzinfo=UTC)


def make_candles(count: int, *, interval_minutes: int = 60, skip: set[int] | None = None):
    """Kerzenliste erzeugen; ``skip`` laesst gezielt Indizes aus (Luecken)."""
    skip = skip or set()
    candles = []
    for i in range(count):
        if i in skip:
            continue
        open_time = BASE_TIME + timedelta(minutes=interval_minutes * i)
        candles.append(
            Candle(
                open_time=open_time,
                close_time=open_time + timedelta(minutes=interval_minutes),
                open=100.0 + i,
                high=101.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                volume=10.0,
            )
        )
    return candles


class TestCandleSeries:
    def test_dataframe_has_expected_shape(self) -> None:
        series = CandleSeries("BTCUSDT", "1h", make_candles(10))
        df = series.to_dataframe()
        assert len(df) == 10
        assert list(df.columns[:5]) == ["open", "high", "low", "close", "volume"]

    def test_dataframe_index_is_sorted_utc(self) -> None:
        series = CandleSeries("BTCUSDT", "1h", list(reversed(make_candles(10))))
        df = series.to_dataframe()
        assert df.index.is_monotonic_increasing
        assert str(df.index.tz) == "UTC"

    def test_empty_series_yields_empty_dataframe(self) -> None:
        df = CandleSeries("BTCUSDT", "1h", []).to_dataframe()
        assert df.empty
        assert "close" in df.columns

    def test_last_close_reflects_final_candle(self) -> None:
        series = CandleSeries("BTCUSDT", "1h", make_candles(5))
        assert series.last_close == pytest.approx(104.5)

    def test_interval_matches_timeframe(self) -> None:
        assert CandleSeries("BTCUSDT", "4h", []).interval == timedelta(hours=4)


class TestDataQuality:
    def test_full_history_without_gaps_scores_high(self) -> None:
        series = CandleSeries("BTCUSDT", "1h", make_candles(300))
        assert series.data_quality(min_candles=210) == pytest.approx(100.0)

    def test_empty_series_scores_zero(self) -> None:
        assert CandleSeries("BTCUSDT", "1h", []).data_quality(min_candles=210) == 0.0

    def test_short_history_lowers_score(self) -> None:
        short = CandleSeries("BTCUSDT", "1h", make_candles(100))
        full = CandleSeries("BTCUSDT", "1h", make_candles(300))
        assert short.data_quality(min_candles=210) < full.data_quality(min_candles=210)

    def test_gaps_lower_score(self) -> None:
        clean = CandleSeries("BTCUSDT", "1h", make_candles(300))
        gappy = CandleSeries("BTCUSDT", "1h", make_candles(300), missing_candles=50)
        assert gappy.data_quality(min_candles=210) < clean.data_quality(min_candles=210)

    def test_score_stays_within_bounds(self) -> None:
        series = CandleSeries("BTCUSDT", "1h", make_candles(50), missing_candles=500)
        assert 0.0 <= series.data_quality(min_candles=210) <= 100.0


def binance_kline(index: int, interval_minutes: int = 60) -> list[object]:
    """Eine Kline im Binance-Array-Format."""
    open_time = BASE_TIME + timedelta(minutes=interval_minutes * index)
    close_time = open_time + timedelta(minutes=interval_minutes) - timedelta(milliseconds=1)
    return [
        datetime_to_ms(open_time),
        f"{100.0 + index}",
        f"{101.0 + index}",
        f"{99.0 + index}",
        f"{100.5 + index}",
        "10.0",
        datetime_to_ms(close_time),
        "1005.0",
        42,
        "5.0",
        "502.5",
        "0",
    ]


EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "status": "TRADING",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00001000"},
            ],
        },
        {
            "symbol": "ETHUSDT",
            "baseAsset": "ETH",
            "quoteAsset": "USDT",
            "status": "TRADING",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00010000"},
            ],
        },
    ]
}


def make_provider(handler) -> BinanceMarketDataProvider:  # type: ignore[no-untyped-def]
    """Provider mit einem httpx-MockTransport statt echter Verbindung."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.binance.com"
    )
    provider = BinanceMarketDataProvider(NO_RETRY_SETTINGS, client=client)
    # Der Provider besitzt den Client nicht, schliesst ihn also nicht selbst.
    provider._owns_client = True
    return provider


class TestBinanceProvider:
    @pytest.mark.asyncio
    async def test_fetches_symbol_info(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "exchangeInfo" in request.url.path
            return httpx.Response(200, json=EXCHANGE_INFO)

        provider = make_provider(handler)
        try:
            info = await provider.get_symbol_info("BTCUSDT")
        finally:
            await provider.close()

        assert isinstance(info, SymbolInfo)
        assert info.base_asset == "BTC"
        assert info.quote_asset == "USDT"
        # tickSize 0.01 entspricht zwei Nachkommastellen.
        assert info.price_precision == 2
        assert info.quantity_precision == 5

    @pytest.mark.asyncio
    async def test_unknown_symbol_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"symbols": []})

        provider = make_provider(handler)
        try:
            with pytest.raises(SymbolNotFoundError):
                await provider.get_symbol_info("DOESNOTEXIST")
        finally:
            await provider.close()

    @pytest.mark.asyncio
    async def test_lists_available_symbols(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=EXCHANGE_INFO)

        provider = make_provider(handler)
        try:
            symbols = await provider.list_symbols(quote_asset="USDT")
        finally:
            await provider.close()

        assert {s.symbol for s in symbols} == {"BTCUSDT", "ETHUSDT"}

    @pytest.mark.asyncio
    async def test_fetches_current_price(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "42350.12"})

        provider = make_provider(handler)
        try:
            price = await provider.get_price("BTCUSDT")
        finally:
            await provider.close()

        assert price == pytest.approx(42350.12)

    @pytest.mark.asyncio
    async def test_fetches_multiple_prices_in_one_request(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(
                200,
                json=[
                    {"symbol": "BTCUSDT", "price": "42350.12"},
                    {"symbol": "ETHUSDT", "price": "2280.40"},
                ],
            )

        provider = make_provider(handler)
        try:
            prices = await provider.get_prices(["BTCUSDT", "ETHUSDT"])
        finally:
            await provider.close()

        assert prices == {"BTCUSDT": pytest.approx(42350.12), "ETHUSDT": pytest.approx(2280.40)}
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_normalizes_klines_to_candles(self) -> None:
        payload = [binance_kline(i) for i in range(5)]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        provider = make_provider(handler)
        try:
            series = await provider.get_candles("BTCUSDT", "1h", limit=5)
        finally:
            await provider.close()

        assert len(series) == 5
        first = series.candles[0]
        assert first.open_time == BASE_TIME
        assert first.open == pytest.approx(100.0)
        assert first.close == pytest.approx(100.5)
        assert first.volume == pytest.approx(10.0)
        assert first.trade_count == 42
        assert series.candles[0].open_time.tzinfo is not None

    @pytest.mark.asyncio
    async def test_detects_missing_candles(self) -> None:
        """Eine Luecke in der Taktung muss erkannt und gezaehlt werden."""
        payload = [binance_kline(i) for i in (0, 1, 2, 5, 6)]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        provider = make_provider(handler)
        try:
            series = await provider.get_candles("BTCUSDT", "1h", limit=10)
        finally:
            await provider.close()

        assert series.missing_candles == 2
        assert series.gaps

    @pytest.mark.asyncio
    async def test_raises_on_rate_limit(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"code": -1003, "msg": "Too many requests"})

        provider = make_provider(handler)
        try:
            with pytest.raises(MarketDataError):
                await provider.get_candles("BTCUSDT", "1h", limit=5)
        finally:
            await provider.close()

    @pytest.mark.asyncio
    async def test_raises_on_server_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal error")

        provider = make_provider(handler)
        try:
            with pytest.raises(MarketDataError):
                await provider.get_candles("BTCUSDT", "1h", limit=5)
        finally:
            await provider.close()

    @pytest.mark.asyncio
    async def test_health_check_reports_failure_without_raising(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="unavailable")

        provider = make_provider(handler)
        try:
            assert await provider.health_check() is False
        finally:
            await provider.close()

    @pytest.mark.asyncio
    async def test_empty_response_yields_empty_series(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        provider = make_provider(handler)
        try:
            series = await provider.get_candles("BTCUSDT", "1h", limit=5)
        finally:
            await provider.close()

        assert series.is_empty
        assert series.data_quality(min_candles=210) == 0.0
