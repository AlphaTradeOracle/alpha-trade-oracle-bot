"""Marktdaten-Schicht: austauschbare Provider hinter einem gemeinsamen Interface."""

from app.market_data.base import MarketDataProvider
from app.market_data.binance import BinanceMarketDataProvider
from app.market_data.cache import CachedMarketDataProvider
from app.market_data.coingecko import CoinGeckoClient, CoinGeckoMarket
from app.market_data.factory import available_providers, create_market_data_provider
from app.market_data.kucoin import KucoinMarketDataProvider
from app.market_data.types import Candle, CandleSeries, SymbolInfo

__all__ = [
    "BinanceMarketDataProvider",
    "CachedMarketDataProvider",
    "Candle",
    "CandleSeries",
    "CoinGeckoClient",
    "CoinGeckoMarket",
    "KucoinMarketDataProvider",
    "MarketDataProvider",
    "SymbolInfo",
    "available_providers",
    "create_market_data_provider",
]
