"""Interfaces der Sentiment-Quellen.

Das Modul ist standardmaessig deaktiviert (``ENABLE_SENTIMENT=false``). Die
Interfaces sind vorbereitet, damit spaeter Datenquellen ergaenzt werden koennen,
ohne die Signal-Engine zu aendern.

Grundregel: Liegen keine verlaesslichen Daten vor, wird ``None`` zurueckgegeben —
niemals ein geschaetzter oder erfundener Wert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SentimentReading:
    """Ein einzelner Sentiment-Messwert.

    ``value`` liegt in [-100, +100]: negativ = Angst/baerisch,
    positiv = Gier/bullisch.
    """

    source: str
    value: float
    captured_at: datetime
    detail: str = ""
    #: 0..1 — wie belastbar der Wert ist. Fliesst in die Gewichtung ein.
    reliability: float = 1.0


@runtime_checkable
class SentimentSource(Protocol):
    """Vertrag einer Sentiment-Datenquelle."""

    name: str

    async def fetch(self, symbol: str) -> SentimentReading | None:
        """Messwert liefern, oder ``None`` wenn keine Daten verfuegbar sind."""
        ...

    async def close(self) -> None: ...


@runtime_checkable
class FearGreedSource(SentimentSource, Protocol):
    """Fear-and-Greed-Index des Gesamtmarkts."""


@runtime_checkable
class NewsSentimentSource(SentimentSource, Protocol):
    """Sentiment aus Nachrichtenmeldungen."""


@runtime_checkable
class SocialSentimentSource(SentimentSource, Protocol):
    """Sentiment aus sozialen Medien."""


@runtime_checkable
class MarketStructureSource(SentimentSource, Protocol):
    """Bitcoin-Dominanz und Gesamtmarktkapitalisierung."""


@runtime_checkable
class DerivativesSource(SentimentSource, Protocol):
    """Funding Rates und Open Interest."""
