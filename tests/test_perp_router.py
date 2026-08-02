"""Unit tests for perpetual price routing."""

from __future__ import annotations

import pytest

from app.core.errors import SymbolNotFoundError
from app.market_data.perp_router import PerpRouterProvider, _parse_venue_order
from app.market_data.types import CandleSeries, SymbolInfo


class _FakeVenue:
    def __init__(self, name: str, bases: set[str], price: float = 100.0) -> None:
        self.name = name
        self._bases = bases
        self._price = price
        self.candle_calls: list[str] = []

    async def supports_base(self, base: str) -> bool:
        return base.upper() in self._bases

    async def get_price(self, symbol: str) -> float:
        if not await self.supports_base(symbol.replace("USDT", "")):
            raise SymbolNotFoundError(symbol)
        return self._price

    async def get_prices(self, symbols: list[str]) -> dict[str, float]:
        out = {}
        for s in symbols:
            try:
                out[s.upper()] = await self.get_price(s)
            except SymbolNotFoundError:
                continue
        return out

    async def get_candles(self, symbol, timeframe, **kwargs):
        self.candle_calls.append(symbol)
        return CandleSeries(symbol=symbol, timeframe=timeframe, candles=[], source=self.name)

    async def get_multi_timeframe_candles(self, symbol, timeframes, *, limit=500):
        return {}

    async def list_symbols(self, quote_asset=None):
        return [
            SymbolInfo(symbol=f"{b}USDT", base_asset=b, quote_asset="USDT")
            for b in sorted(self._bases)
        ]

    async def get_symbol_info(self, symbol):
        return SymbolInfo(symbol=symbol, base_asset=symbol.replace("USDT", ""), quote_asset="USDT")

    async def health_check(self):
        return True

    async def close(self):
        return None


def test_parse_venue_order_aliases():
    assert _parse_venue_order("binance_futures,kucoin,hl") == [
        "binance",
        "kucoin",
        "hyperliquid",
    ]


@pytest.mark.asyncio
async def test_router_prefers_binance_then_fallback():
    binance = _FakeVenue("binance_futures", {"BTC", "ETH"}, price=1.0)
    kucoin = _FakeVenue("kucoin_futures", {"DODO", "ETH"}, price=2.0)
    aster = _FakeVenue("aster_futures", {"ANSEM"}, price=3.0)
    router = PerpRouterProvider(
        venues={
            "binance": binance,
            "kucoin": kucoin,
            "aster": aster,
        },
        venue_order=["binance", "kucoin", "aster"],
    )

    assert await router.get_price("BTCUSDT") == 1.0
    assert await router.get_price("DODOUSDT") == 2.0
    assert await router.get_price("ANSEMUSDT") == 3.0
    # ETH on both — first venue wins
    assert await router.get_price("ETHUSDT") == 1.0

    series = await router.get_candles("DODOUSDT", "5m", limit=1)
    assert series.source == "perp:kucoin_futures"
    assert kucoin.candle_calls == ["DODOUSDT"]


@pytest.mark.asyncio
async def test_router_unroutable_raises():
    router = PerpRouterProvider(
        venues={"binance": _FakeVenue("binance_futures", {"BTC"})},
        venue_order=["binance"],
    )
    with pytest.raises(SymbolNotFoundError):
        await router.get_price("ZZZUSDT")
