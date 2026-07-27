"""Provider-unabhaengiges Interface der Marktdaten-Schicht.

Die Domaenenlogik haengt nur von diesem Protocol ab, nie von Binance. Ein
weiterer Provider (Bybit, Kraken, CoinGecko) muss lediglich diese Methoden
implementieren, um eingesetzt werden zu koennen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.market_data.types import CandleSeries, SymbolInfo


@runtime_checkable
class MarketDataProvider(Protocol):
    """Vertrag fuer jede Marktdatenquelle."""

    name: str

    async def list_symbols(self, quote_asset: str | None = None) -> list[SymbolInfo]:
        """Verfuegbare Handelspaare, optional auf ein Quote-Asset gefiltert."""
        ...

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Metadaten eines Symbols. Wirft ``SymbolNotFoundError`` bei Unbekanntem."""
        ...

    async def get_price(self, symbol: str) -> float:
        """Aktueller Kurs."""
        ...

    async def get_prices(self, symbols: list[str]) -> dict[str, float]:
        """Aktuelle Kurse mehrerer Symbole in einem Aufruf."""
        ...

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
        """OHLCV-Kerzen. Unfertige Kerzen werden standardmaessig verworfen."""
        ...

    async def get_multi_timeframe_candles(
        self, symbol: str, timeframes: list[str], *, limit: int = 500
    ) -> dict[str, CandleSeries]:
        """Mehrere Timeframes nebenlaeufig laden."""
        ...

    async def health_check(self) -> bool:
        """Erreichbarkeitspruefung fuer das Monitoring."""
        ...

    async def close(self) -> None:
        """Netzwerkressourcen freigeben."""
        ...
