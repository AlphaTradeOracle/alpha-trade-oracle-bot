"""Tests fuer CoinGecko-Client, UniverseService und Universe-Batch-Scan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import MarketDataError, SymbolNotFoundError
from app.market_data.coingecko import (
    CoinGeckoClient,
    CoinGeckoMarket,
    CoinGeckoTicker,
    exchange_matches_provider,
)
from app.market_data.types import Candle, CandleSeries, SymbolInfo
from app.models.market import Asset
from app.repositories.asset_repository import AssetRepository
from app.services.analysis_service import AnalysisService
from app.services.scan_service import ScanService
from app.services.universe_service import SKIP_BASE_ASSETS, UniverseService
from app.signals.dedup import SignalDeduplicator
from tests.test_services import RecordingDispatcher, StubProvider, service_settings


@pytest.fixture
def uptrend_frames(uptrend_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return dict.fromkeys(("15m", "1h", "4h", "1d"), uptrend_df)


NO_RETRY = Settings(http_max_retries=0, enable_llm_analysis=False)


BTC = SymbolInfo(
    symbol="BTCUSDT",
    base_asset="BTC",
    quote_asset="USDT",
    price_precision=2,
    quantity_precision=6,
    is_active=True,
)
ETH = SymbolInfo(
    symbol="ETHUSDT",
    base_asset="ETH",
    quote_asset="USDT",
    price_precision=2,
    quantity_precision=5,
    is_active=True,
)
SOL = SymbolInfo(
    symbol="SOLUSDT",
    base_asset="SOL",
    quote_asset="USDT",
    price_precision=2,
    quantity_precision=2,
    is_active=True,
)


def _cg_market(
    coin_id: str, symbol: str, rank: int, *, market_cap: float = 1_000_000.0
) -> dict[str, object]:
    return {
        "id": coin_id,
        "symbol": symbol,
        "name": coin_id.title(),
        "market_cap": market_cap,
        "market_cap_rank": rank,
    }


def _cg_live_market(
    coin_id: str,
    symbol: str,
    rank: int,
    *,
    price: float = 100.0,
    change: float = 1.5,
) -> dict[str, object]:
    return {
        "id": coin_id,
        "symbol": symbol,
        "name": coin_id.title(),
        "market_cap": 1_000_000.0,
        "market_cap_rank": rank,
        "current_price": price,
        "price_change_percentage_24h": change,
        "total_volume": 50_000.0,
        "circulating_supply": 21_000_000.0,
        "image": f"https://example.com/{symbol}.png",
        "sparkline_in_7d": {"price": [price * 0.9, price, price * 1.05]},
    }


class TestCoinGeckoClient:
    @pytest.mark.asyncio
    async def test_fetches_paginated_top_markets(self) -> None:
        pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/coins/markets")
            page = int(request.url.params.get("page", "1"))
            per_page = int(request.url.params.get("per_page", "250"))
            pages.append(page)
            if page == 1:
                payload = [
                    _cg_market("bitcoin", "btc", 1),
                    _cg_market("ethereum", "eth", 2),
                ][:per_page]
            else:
                payload = [_cg_market("solana", "sol", 3)][:per_page]
            return httpx.Response(200, json=payload)

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.coingecko.com/api/v3",
        )
        gecko = CoinGeckoClient(NO_RETRY, client=client)
        gecko._owns_client = True
        try:
            import app.market_data.coingecko as cg_mod

            original = cg_mod.MAX_PER_PAGE
            cg_mod.MAX_PER_PAGE = 2
            try:
                markets = await gecko.fetch_top_markets(limit=3)
            finally:
                cg_mod.MAX_PER_PAGE = original
        finally:
            await gecko.close()

        assert [m.symbol for m in markets] == ["BTC", "ETH", "SOL"]
        assert pages == [1, 2]

    @pytest.mark.asyncio
    async def test_pagination_continues_when_null_rank_drops_parsed_count(self) -> None:
        """Regression: a full raw page with one null-rank row must not stop paging."""
        pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page", "1"))
            pages.append(page)
            if page == 1:
                # Full page (MAX_PER_PAGE mocked to 3): one null-rank drop → parsed=2.
                payload = [
                    _cg_market("bitcoin", "btc", 1),
                    {
                        "id": "mystery",
                        "symbol": "xyz",
                        "name": "Mystery",
                        "market_cap": 1.0,
                        "market_cap_rank": None,
                    },
                    _cg_market("ethereum", "eth", 2),
                ]
            else:
                payload = [_cg_market("solana", "sol", 3)]
            return httpx.Response(200, json=payload)

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.coingecko.com/api/v3",
        )
        gecko = CoinGeckoClient(NO_RETRY, client=client)
        gecko._owns_client = True
        try:
            import app.market_data.coingecko as cg_mod

            original = cg_mod.MAX_PER_PAGE
            cg_mod.MAX_PER_PAGE = 3
            try:
                markets = await gecko.fetch_top_markets(limit=3)
            finally:
                cg_mod.MAX_PER_PAGE = original
        finally:
            await gecko.close()

        assert [m.symbol for m in markets] == ["BTC", "ETH", "SOL"]
        assert pages == [1, 2]

    @pytest.mark.asyncio
    async def test_fetches_live_markets_with_sparkline(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params.get("sparkline") == "true"
            return httpx.Response(
                200,
                json=[
                    _cg_live_market("bitcoin", "btc", 1, price=65000.0, change=-1.2),
                    _cg_live_market("figure-heloc", "figr_heloc", 9, price=1.0, change=-1.0),
                    _cg_live_market("whitebit", "wbt", 18, price=50.0, change=0.2),
                    _cg_live_market("tether", "usdt", 3, price=1.0, change=0.01),
                    _cg_live_market("hyperliquid", "hype", 10, price=55.0, change=1.1),
                ],
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.coingecko.com/api/v3",
        )
        gecko = CoinGeckoClient(NO_RETRY, client=client)
        gecko._owns_client = True
        try:
            markets = await gecko.fetch_live_markets(limit=10)
        finally:
            await gecko.close()

        assert [m.symbol for m in markets] == ["BTC", "USDT", "HYPE"]
        assert [m.market_cap_rank for m in markets] == [1, 2, 3]
        assert markets[0].price_usd == 65000.0
        assert markets[0].change_24h_pct == pytest.approx(-1.2)
        assert markets[0].sparkline == (65000.0 * 0.9, 65000.0, 65000.0 * 1.05)
        assert markets[1].image_url is not None
        assert markets[1].image_url.endswith("usdt.png")

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="rate limited")

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.coingecko.com/api/v3",
        )
        gecko = CoinGeckoClient(NO_RETRY, client=client)
        gecko._owns_client = True
        try:
            with pytest.raises(MarketDataError):
                await gecko.fetch_top_markets(limit=10)
        finally:
            await gecko.close()

    @pytest.mark.asyncio
    async def test_fetches_coin_tickers(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/coins/arbitrum/tickers")
            return httpx.Response(
                200,
                json={
                    "tickers": [
                        {
                            "base": "ARB",
                            "target": "USDT",
                            "market": {"name": "KuCoin", "identifier": "kucoin"},
                        },
                        {
                            "base": "ARB",
                            "target": "USDT",
                            "market": {"name": "Binance", "identifier": "binance"},
                        },
                    ]
                },
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.coingecko.com/api/v3",
        )
        gecko = CoinGeckoClient(NO_RETRY, client=client)
        gecko._owns_client = True
        try:
            tickers = await gecko.fetch_coin_tickers("arbitrum")
        finally:
            await gecko.close()

        assert len(tickers) == 2
        assert tickers[0].base == "ARB"
        assert tickers[0].target == "USDT"
        assert tickers[0].market_identifier == "kucoin"


class TestExchangeMatching:
    def test_matches_kucoin_identifier(self) -> None:
        ticker = CoinGeckoTicker("BTC", "USDT", "KuCoin", "kucoin")
        assert exchange_matches_provider(ticker, "kucoin") is True
        assert exchange_matches_provider(ticker, "binance") is False

    def test_matches_binance_name(self) -> None:
        ticker = CoinGeckoTicker("ETH", "USDT", "Binance", "binance")
        assert exchange_matches_provider(ticker, "binance") is True


class MultiSymbolStubProvider(StubProvider):
    def __init__(
        self,
        frames: dict[str, pd.DataFrame],
        infos: list[SymbolInfo],
    ) -> None:
        super().__init__(frames, info=infos[0])
        self._infos = {info.symbol: info for info in infos}
        self.analyze_llm_flags: list[bool | None] = []

    async def list_symbols(self, quote_asset: str | None = None) -> list[SymbolInfo]:
        values = list(self._infos.values())
        if quote_asset:
            wanted = quote_asset.upper()
            values = [info for info in values if info.quote_asset == wanted]
        return values

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        info = self._infos.get(symbol.upper())
        if info is None:
            from app.core.errors import SymbolNotFoundError

            raise SymbolNotFoundError(symbol)
        return info


class NamedStubProvider(MultiSymbolStubProvider):
    def __init__(self, name: str, frames: dict[str, pd.DataFrame], infos: list[SymbolInfo]) -> None:
        super().__init__(frames, infos)
        self.name = name


class TradabilityStubProvider(MultiSymbolStubProvider):
    """Liefert fuer ausgewaehlte Symbole keine bzw. nur duenne Kerzen."""

    def __init__(
        self,
        frames: dict[str, pd.DataFrame],
        infos: list[SymbolInfo],
        *,
        without_candles: set[str] | None = None,
        unknown: set[str] | None = None,
        thin: set[str] | None = None,
    ) -> None:
        super().__init__(frames, infos)
        self._without_candles = without_candles or set()
        self._unknown = unknown or set()
        self._thin = thin or set()

    async def get_candles(self, symbol: str, timeframe: str, **kwargs: object) -> CandleSeries:
        upper = symbol.upper()
        if upper in self._unknown:
            raise SymbolNotFoundError(upper)
        if upper in self._without_candles:
            return CandleSeries(symbol=upper, timeframe=timeframe, candles=[])
        if upper in self._thin:
            start = datetime(2024, 1, 1, tzinfo=UTC)
            candles = [
                Candle(
                    open_time=start + timedelta(hours=i),
                    close_time=start + timedelta(hours=i + 1),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=100.0,
                )
                for i in range(24)
            ]
            return CandleSeries(symbol=upper, timeframe=timeframe, candles=candles)
        return await super().get_candles(symbol, timeframe, **kwargs)  # type: ignore[arg-type]


class TestUniverseTradabilityGate:
    @pytest.mark.asyncio
    async def test_symbol_without_candles_is_not_activated(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        provider = TradabilityStubProvider(
            uptrend_frames, [BTC, ETH], without_candles={"ETHUSDT"}
        )
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[
                CoinGeckoMarket("bitcoin", "BTC", "Bitcoin", 90.0, 1),
                CoinGeckoMarket("ethereum", "ETH", "Ethereum", 80.0, 2),
            ]
        )
        service = UniverseService(
            provider,
            gecko,
            settings=service_settings(universe_size=10, universe_verify_candles=True),
        )

        result = await service.refresh(session)

        assert result.mapped == 1
        assert result.skipped_no_candles == 1
        assert await AssetRepository(session).get_by_symbol("ETHUSDT") is None

    @pytest.mark.asyncio
    async def test_symbol_unknown_to_candle_endpoint_is_not_activated(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        provider = TradabilityStubProvider(uptrend_frames, [BTC, ETH], unknown={"ETHUSDT"})
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[
                CoinGeckoMarket("bitcoin", "BTC", "Bitcoin", 90.0, 1),
                CoinGeckoMarket("ethereum", "ETH", "Ethereum", 80.0, 2),
            ]
        )
        service = UniverseService(
            provider,
            gecko,
            settings=service_settings(universe_size=10, universe_verify_candles=True),
        )

        result = await service.refresh(session)

        assert result.mapped == 1
        assert result.skipped_no_candles == 1
        assert await AssetRepository(session).get_by_symbol("ETHUSDT") is None

    @pytest.mark.asyncio
    async def test_provider_outage_keeps_symbol(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        """Ein API-Aussetzer darf nicht das halbe Universe deaktivieren."""

        class FlakyProvider(TradabilityStubProvider):
            async def get_candles(
                self, symbol: str, timeframe: str, **kwargs: object
            ) -> CandleSeries:
                if symbol.upper() == "ETHUSDT":
                    raise MarketDataError("KuCoin antwortet nicht")
                return await super().get_candles(symbol, timeframe, **kwargs)

        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[
                CoinGeckoMarket("bitcoin", "BTC", "Bitcoin", 90.0, 1),
                CoinGeckoMarket("ethereum", "ETH", "Ethereum", 80.0, 2),
            ]
        )
        service = UniverseService(
            FlakyProvider(uptrend_frames, [BTC, ETH]),
            gecko,
            settings=service_settings(universe_size=10, universe_verify_candles=True),
        )

        result = await service.refresh(session)

        assert result.mapped == 2
        assert result.skipped_no_candles == 0

    @pytest.mark.asyncio
    async def test_thinly_traded_symbol_is_filtered(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        provider = TradabilityStubProvider(uptrend_frames, [BTC, ETH], thin={"ETHUSDT"})
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[
                CoinGeckoMarket("bitcoin", "BTC", "Bitcoin", 90.0, 1),
                CoinGeckoMarket("ethereum", "ETH", "Ethereum", 80.0, 2),
            ]
        )
        service = UniverseService(
            provider,
            gecko,
            settings=service_settings(
                universe_size=10,
                universe_verify_candles=True,
                universe_min_quote_volume_usd=1_000_000.0,
            ),
        )

        result = await service.refresh(session)

        assert result.mapped == 1
        assert result.skipped_illiquid == 1
        assert await AssetRepository(session).get_by_symbol("ETHUSDT") is None


class TrackingAnalysisService(AnalysisService):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.use_llm_calls: list[bool | None] = []

    async def analyze(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        self.use_llm_calls.append(kwargs.get("use_llm"))
        return await super().analyze(*args, **kwargs)


class TestUniverseService:
    @pytest.mark.asyncio
    async def test_maps_exchange_pairs_and_skips_stables(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        provider = MultiSymbolStubProvider(uptrend_frames, [BTC, ETH, SOL])
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[
                CoinGeckoMarket("tether", "USDT", "Tether", 100.0, 1),
                CoinGeckoMarket("bitcoin", "BTC", "Bitcoin", 90.0, 2),
                CoinGeckoMarket("ethereum", "ETH", "Ethereum", 80.0, 3),
                CoinGeckoMarket("not-listed", "XYZ", "Xyz", 70.0, 4),
            ]
        )
        settings = service_settings(universe_size=10, enable_universe_scan=True)
        service = UniverseService(provider, gecko, settings=settings)

        result = await service.refresh(session)

        assert result.mapped == 2
        assert result.skipped_stable == 1
        assert result.skipped_no_pair == 1
        assert set(result.symbols) == {"BTCUSDT", "ETHUSDT"}

        assets = await AssetRepository(session).list_universe()
        assert [asset.symbol for asset in assets] == ["BTCUSDT", "ETHUSDT"]
        assert assets[0].market_cap_rank == 2
        assert assets[0].coingecko_id == "bitcoin"
        assert assets[0].in_universe is True

    @pytest.mark.asyncio
    async def test_skips_duplicate_symbol_mappings(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        """Zwei CoinGecko-Eintraege mit gleichem Boersen-Symbol zaehlen nur einmal."""
        provider = MultiSymbolStubProvider(uptrend_frames, [BTC, ETH])
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[
                CoinGeckoMarket("bitcoin", "BTC", "Bitcoin", 90.0, 1),
                CoinGeckoMarket("bitcoin-fork", "BTC", "Bitcoin Fork", 80.0, 2),
                CoinGeckoMarket("ethereum", "ETH", "Ethereum", 70.0, 3),
            ]
        )
        settings = service_settings(
            universe_size=10,
            universe_target_count=2,
            enable_universe_scan=True,
        )
        service = UniverseService(provider, gecko, settings=settings)

        result = await service.refresh(session)

        assert result.mapped == 2
        assert result.skipped_duplicate == 1
        assets = await AssetRepository(session).list_universe()
        assert len(assets) == 2

    @pytest.mark.asyncio
    async def test_stops_at_universe_target_count(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        provider = MultiSymbolStubProvider(uptrend_frames, [BTC, ETH, SOL])
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[
                CoinGeckoMarket("bitcoin", "BTC", "Bitcoin", 90.0, 1),
                CoinGeckoMarket("ethereum", "ETH", "Ethereum", 80.0, 2),
                CoinGeckoMarket("solana", "SOL", "Solana", 70.0, 3),
            ]
        )
        settings = service_settings(
            universe_size=10,
            universe_target_count=2,
            enable_universe_scan=True,
        )
        service = UniverseService(provider, gecko, settings=settings)

        result = await service.refresh(session)

        assert result.mapped == 2
        assert set(result.symbols) == {"BTCUSDT", "ETHUSDT"}
        assets = await AssetRepository(session).list_universe()
        assert len(assets) == 2

    @pytest.mark.asyncio
    async def test_skips_bases_without_leverage(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        provider = MultiSymbolStubProvider(uptrend_frames, [BTC, ETH, SOL])
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[
                CoinGeckoMarket("bitcoin", "BTC", "Bitcoin", 90.0, 1),
                CoinGeckoMarket("ethereum", "ETH", "Ethereum", 80.0, 2),
                CoinGeckoMarket("solana", "SOL", "Solana", 70.0, 3),
            ]
        )
        leverage = AsyncMock()
        leverage.fetch_tradable_bases = AsyncMock(return_value={"BTC", "ETH"})
        leverage.aclose = AsyncMock()
        settings = service_settings(
            universe_size=10,
            universe_target_count=10,
            universe_require_leverage=True,
            enable_universe_scan=True,
        )
        service = UniverseService(provider, gecko, settings=settings, leverage=leverage)

        result = await service.refresh(session)

        assert result.mapped == 2
        assert result.skipped_no_leverage == 1
        assert set(result.symbols) == {"BTCUSDT", "ETHUSDT"}

    @pytest.mark.asyncio
    async def test_skips_symbols_without_perp_route(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        provider = MultiSymbolStubProvider(uptrend_frames, [BTC, ETH, SOL])
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[
                CoinGeckoMarket("bitcoin", "BTC", "Bitcoin", 90.0, 1),
                CoinGeckoMarket("ethereum", "ETH", "Ethereum", 80.0, 2),
                CoinGeckoMarket("solana", "SOL", "Solana", 70.0, 3),
            ]
        )
        leverage = AsyncMock()
        leverage.fetch_tradable_bases = AsyncMock(return_value={"BTC", "ETH", "SOL"})
        leverage.aclose = AsyncMock()

        class _PerpStub:
            async def resolve_venue(self, symbol: str) -> object:
                if symbol == "SOLUSDT":
                    raise SymbolNotFoundError(symbol)
                return object()

        settings = service_settings(
            universe_size=10,
            universe_target_count=10,
            universe_require_leverage=True,
            enable_universe_scan=True,
        )
        service = UniverseService(
            provider,
            gecko,
            settings=settings,
            leverage=leverage,
            perp_provider=_PerpStub(),  # type: ignore[arg-type]
        )

        result = await service.refresh(session)

        assert result.mapped == 2
        assert result.skipped_no_leverage == 1
        assert set(result.symbols) == {"BTCUSDT", "ETHUSDT"}

    @pytest.mark.asyncio
    async def test_deactivates_stale_universe_entries(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        repo = AssetRepository(session)
        await repo.upsert_universe_entry(
            symbol="SOLUSDT",
            base_asset="SOL",
            quote_asset="USDT",
            exchange="stub",
            coingecko_id="solana",
            market_cap_rank=5,
            market_cap_usd=Decimal("50"),
        )

        provider = MultiSymbolStubProvider(uptrend_frames, [BTC, ETH, SOL])
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[CoinGeckoMarket("bitcoin", "BTC", "Bitcoin", 90.0, 1)]
        )
        service = UniverseService(
            provider, gecko, settings=service_settings(universe_size=10, enable_universe_scan=True)
        )

        result = await service.refresh(session)

        assert result.mapped == 1
        assert result.deactivated == 1
        sol = await repo.get_by_symbol("SOLUSDT")
        assert sol is not None
        assert sol.in_universe is False
        assert sol.market_cap_rank is None

    def test_stable_skip_set_contains_usdt(self) -> None:
        assert "USDT" in SKIP_BASE_ASSETS

    @pytest.mark.asyncio
    async def test_prefers_primary_exchange_in_dual_mapping(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        kucoin = NamedStubProvider("kucoin", uptrend_frames, [BTC])
        binance = NamedStubProvider("binance", uptrend_frames, [BTC, ETH])
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[CoinGeckoMarket("bitcoin", "BTC", "Bitcoin", 90.0, 1)]
        )
        settings = service_settings(
            universe_size=10,
            market_data_provider="kucoin",
            universe_exchanges="kucoin,binance",
        )
        service = UniverseService({"kucoin": kucoin, "binance": binance}, gecko, settings=settings)

        result = await service.refresh(session)

        assert result.mapped == 1
        assert result.mapped_via_direct == 1
        asset = await AssetRepository(session).get_by_symbol("BTCUSDT")
        assert asset is not None
        assert asset.exchange == "kucoin"

    @pytest.mark.asyncio
    async def test_maps_binance_only_coin(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        kucoin = NamedStubProvider("kucoin", uptrend_frames, [BTC])
        binance = NamedStubProvider("binance", uptrend_frames, [BTC, ETH])
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[CoinGeckoMarket("ethereum", "ETH", "Ethereum", 80.0, 2)]
        )
        settings = service_settings(
            universe_size=10,
            market_data_provider="kucoin",
            universe_exchanges="kucoin,binance",
        )
        service = UniverseService({"kucoin": kucoin, "binance": binance}, gecko, settings=settings)

        result = await service.refresh(session)

        assert result.mapped == 1
        asset = await AssetRepository(session).get_by_symbol("ETHUSDT")
        assert asset is not None
        assert asset.exchange == "binance"

    @pytest.mark.asyncio
    async def test_ticker_fallback_maps_alias_base(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        pepe = SymbolInfo(
            symbol="PEPEUSDT",
            base_asset="PEPE",
            quote_asset="USDT",
            price_precision=8,
            quantity_precision=0,
            is_active=True,
        )
        kucoin = NamedStubProvider("kucoin", uptrend_frames, [BTC])
        binance = NamedStubProvider("binance", uptrend_frames, [pepe])
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[CoinGeckoMarket("pepe", "1000PEPE", "Pepe", 70.0, 50)]
        )
        gecko.fetch_coin_tickers = AsyncMock(
            return_value=[CoinGeckoTicker("PEPE", "USDT", "Binance", "binance")]
        )
        settings = service_settings(
            universe_size=10,
            market_data_provider="kucoin",
            universe_exchanges="kucoin,binance",
            universe_ticker_fallback=True,
            universe_ticker_fallback_max=10,
        )
        service = UniverseService({"kucoin": kucoin, "binance": binance}, gecko, settings=settings)

        result = await service.refresh(session)

        assert result.mapped == 1
        assert result.mapped_via_ticker == 1
        assert result.ticker_lookups == 1
        asset = await AssetRepository(session).get_by_symbol("PEPEUSDT")
        assert asset is not None
        assert asset.exchange == "binance"

    @pytest.mark.asyncio
    async def test_maps_coinbase_usd_pair(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        render = SymbolInfo(
            symbol="RNDRUSD",
            base_asset="RNDR",
            quote_asset="USD",
            price_precision=4,
            quantity_precision=4,
            is_active=True,
        )
        kucoin = NamedStubProvider("kucoin", uptrend_frames, [BTC])
        binance = NamedStubProvider("binance", uptrend_frames, [BTC])
        coinbase = NamedStubProvider("coinbase", uptrend_frames, [render])
        gecko = AsyncMock()
        gecko.fetch_top_markets = AsyncMock(
            return_value=[CoinGeckoMarket("render-token", "RNDR", "Render", 70.0, 40)]
        )
        settings = service_settings(
            universe_size=10,
            market_data_provider="kucoin",
            universe_exchanges="kucoin,binance,coinbase",
            coinbase_quote_assets="USD,USDC",
        )
        service = UniverseService(
            {"kucoin": kucoin, "binance": binance, "coinbase": coinbase},
            gecko,
            settings=settings,
        )

        result = await service.refresh(session)

        assert result.mapped == 1
        asset = await AssetRepository(session).get_by_symbol("RNDRUSD")
        assert asset is not None
        assert asset.exchange == "coinbase"


class TestAnalysisProviderRouting:
    @pytest.mark.asyncio
    async def test_uses_asset_exchange_from_db(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        await AssetRepository(session).upsert_universe_entry(
            symbol="ETHUSDT",
            base_asset="ETH",
            quote_asset="USDT",
            exchange="binance",
            coingecko_id="ethereum",
            market_cap_rank=2,
            market_cap_usd=Decimal("1"),
        )

        kucoin = NamedStubProvider("kucoin", uptrend_frames, [BTC])
        binance = NamedStubProvider("binance", uptrend_frames, [ETH])
        settings = service_settings(enable_llm_analysis=False)
        analysis = AnalysisService(
            kucoin,
            providers={"kucoin": kucoin, "binance": binance},
            settings=settings,
        )

        outcome = await analysis.analyze("ETHUSDT", session=session, persist=False)

        assert outcome.result.symbol == "ETHUSDT"
        assert any("ETHUSDT" in call for call in binance.calls)
        assert not any("ETHUSDT" in call for call in kucoin.calls)


class TestUniverseBatchScan:
    @pytest.mark.asyncio
    async def test_scan_uses_universe_batch_without_llm(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        repo = AssetRepository(session)
        for symbol, rank, info in (
            ("ETHUSDT", 2, ETH),
            ("BTCUSDT", 1, BTC),
        ):
            await repo.upsert_universe_entry(
                symbol=symbol,
                base_asset=info.base_asset,
                quote_asset=info.quote_asset,
                exchange="stub",
                coingecko_id=symbol.lower(),
                market_cap_rank=rank,
                market_cap_usd=Decimal("1"),
                price_precision=info.price_precision,
                quantity_precision=info.quantity_precision,
            )

        provider = MultiSymbolStubProvider(uptrend_frames, [BTC, ETH])
        settings = service_settings(
            enable_universe_scan=True,
            universe_scan_batch_size=1,
            default_symbols="",
        )
        analysis = TrackingAnalysisService(provider, settings=settings)
        scan = ScanService(
            analysis, SignalDeduplicator(cooldown_minutes=0), settings=settings
        )

        result = await scan.scan(session, dispatch=False)

        assert result.universe_mode is True
        assert result.symbols_scanned == 1
        assert result.signals_created == 1
        assert analysis.use_llm_calls == [False]

        # Round-Robin: zuerst ungescannte mit bestem Rank (BTC).
        btc = await repo.get_by_symbol("BTCUSDT")
        assert btc is not None
        assert btc.last_scanned_at is not None

        result2 = await scan.scan(session, dispatch=False)
        assert result2.symbols_scanned == 1
        eth = await repo.get_by_symbol("ETHUSDT")
        assert eth is not None
        assert eth.last_scanned_at is not None

    @pytest.mark.asyncio
    async def test_universe_dispatches_without_watchlist(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        from app.repositories.chat_repository import ChatRepository

        repo = AssetRepository(session)
        await repo.upsert_universe_entry(
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            exchange="stub",
            coingecko_id="bitcoin",
            market_cap_rank=1,
            market_cap_usd=Decimal("1"),
        )
        await ChatRepository(session).get_or_create(chat_id=111, title="tester")

        provider = MultiSymbolStubProvider(uptrend_frames, [BTC])
        settings = service_settings(
            enable_universe_scan=True,
            universe_scan_batch_size=10,
            signal_min_score=0.0,
            signal_require_strong=False,
            min_risk_reward_ratio=0.01,
            signal_rsi_long_max=100.0,
            signal_rsi_short_min=0.0,
            signal_block_range_market=False,
            signal_min_adx=0.0,
        )
        analysis = AnalysisService(provider, settings=settings)
        dispatcher = RecordingDispatcher()
        scan = ScanService(
            analysis,
            SignalDeduplicator(cooldown_minutes=0),
            dispatcher=dispatcher,
            settings=settings,
        )

        result = await scan.scan(session, dispatch=True, use_universe=True)

        assert result.universe_mode is True
        assert result.signals_dispatched == 1
        assert len(dispatcher.dispatched) == 1

    @pytest.mark.asyncio
    async def test_list_universe_batch_orders_nulls_first(self, session: AsyncSession) -> None:
        repo = AssetRepository(session)
        for symbol, rank in (("AAAUSDT", 2), ("BBBUSDT", 1)):
            await repo.upsert_universe_entry(
                symbol=symbol,
                base_asset=symbol[:3],
                quote_asset="USDT",
                exchange="stub",
                coingecko_id=symbol.lower(),
                market_cap_rank=rank,
                market_cap_usd=Decimal("1"),
            )
        await repo.mark_scanned("BBBUSDT")

        batch = await repo.list_universe_batch(1)
        assert batch[0].symbol == "AAAUSDT"


class TestAssetUniverseRepository:
    @pytest.mark.asyncio
    async def test_upsert_updates_ranking(self, session: AsyncSession) -> None:
        repo = AssetRepository(session)
        await repo.upsert_universe_entry(
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            exchange="binance",
            coingecko_id="bitcoin",
            market_cap_rank=2,
            market_cap_usd=Decimal("100"),
        )
        await repo.upsert_universe_entry(
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            exchange="binance",
            coingecko_id="bitcoin",
            market_cap_rank=1,
            market_cap_usd=Decimal("200"),
        )

        asset = await repo.get_by_symbol("BTCUSDT")
        assert asset is not None
        assert asset.market_cap_rank == 1
        assert asset.market_cap_usd == Decimal("200")
        assert isinstance(asset, Asset)
