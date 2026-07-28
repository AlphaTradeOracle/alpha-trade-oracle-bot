"""Tests fuer CoinGecko-Client, UniverseService und Universe-Batch-Scan."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import MarketDataError
from app.market_data.coingecko import (
    CoinGeckoClient,
    CoinGeckoMarket,
    CoinGeckoTicker,
    exchange_matches_provider,
)
from app.market_data.types import SymbolInfo
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
    async def test_universe_dispatch_only_for_watchlist(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        from app.repositories.chat_repository import ChatRepository, WatchlistRepository

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
        asset = await repo.get_by_symbol("BTCUSDT")
        assert asset is not None

        chat = await ChatRepository(session).get_or_create(chat_id=111, title="tester")
        await WatchlistRepository(session).add(chat.id, asset.id)

        provider = MultiSymbolStubProvider(uptrend_frames, [BTC])
        settings = service_settings(
            enable_universe_scan=True,
            universe_scan_batch_size=10,
            signal_min_score=0.0,
            min_risk_reward_ratio=0.01,
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
        if result.signals_dispatched:
            assert len(dispatcher.dispatched) == 1

    @pytest.mark.asyncio
    async def test_universe_without_watchlist_does_not_dispatch(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
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

        provider = MultiSymbolStubProvider(uptrend_frames, [BTC])
        settings = service_settings(
            enable_universe_scan=True,
            universe_scan_batch_size=10,
            signal_min_score=0.0,
            min_risk_reward_ratio=0.01,
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
        assert result.signals_dispatched == 0
        assert dispatcher.dispatched == []

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
