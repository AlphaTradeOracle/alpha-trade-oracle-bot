"""Datentypen der Marktdaten-Schicht."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from app.core.time import timeframe_to_timedelta


@dataclass(frozen=True)
class SymbolInfo:
    """Metadaten eines Handelspaares."""

    symbol: str
    base_asset: str
    quote_asset: str
    price_precision: int = 2
    quantity_precision: int = 6
    is_active: bool = True


@dataclass(frozen=True)
class Candle:
    """Eine einzelne OHLCV-Kerze. Zeiten immer UTC."""

    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float | None = None
    trade_count: int | None = None
    is_closed: bool = True


@dataclass
class CandleSeries:
    """Zeitreihe eines Symbols und Timeframes samt Qualitaetsangaben."""

    symbol: str
    timeframe: str
    candles: list[Candle]
    #: Anzahl fehlender Kerzen, die anhand der erwarteten Taktung erkannt wurden.
    missing_candles: int = 0
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    source: str = "provider"

    def __len__(self) -> int:
        return len(self.candles)

    @property
    def is_empty(self) -> bool:
        return not self.candles

    @property
    def last_close(self) -> float | None:
        return self.candles[-1].close if self.candles else None

    @property
    def interval(self) -> timedelta:
        return timeframe_to_timedelta(self.timeframe)

    def to_dataframe(self) -> pd.DataFrame:
        """OHLCV-DataFrame mit UTC-DatetimeIndex, aufsteigend sortiert."""
        if not self.candles:
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume", "quote_volume", "trade_count"]
            )

        frame = pd.DataFrame(
            {
                "open_time": [c.open_time for c in self.candles],
                "open": [c.open for c in self.candles],
                "high": [c.high for c in self.candles],
                "low": [c.low for c in self.candles],
                "close": [c.close for c in self.candles],
                "volume": [c.volume for c in self.candles],
                "quote_volume": [c.quote_volume for c in self.candles],
                "trade_count": [c.trade_count for c in self.candles],
            }
        )
        frame = frame.set_index("open_time").sort_index()
        frame.index = pd.DatetimeIndex(frame.index, name="open_time")
        return frame

    def data_quality(self, *, min_candles: int) -> float:
        """Datenqualitaet 0..100 aus Historienlaenge und Luecken.

        Beide Faktoren werden getrennt bewertet: eine kurze aber lueckenlose
        Historie ist etwas anderes als eine lange mit vielen Luecken.
        """
        if not self.candles:
            return 0.0

        length_factor = min(1.0, len(self.candles) / max(min_candles, 1))
        expected = len(self.candles) + self.missing_candles
        gap_factor = len(self.candles) / expected if expected else 1.0
        return round(max(0.0, min(1.0, length_factor * 0.4 + gap_factor * 0.6)) * 100.0, 2)
