"""Sentiment-Service: aggregiert mehrere Quellen zu einem Wert.

Standardmaessig sind keine Quellen registriert. Der Service liefert dann ``None``,
und die Signal-Engine bewertet die Kategorie neutral beziehungsweise verteilt ihr
Gewicht um. Sentiment kann ein technisches Signal beeinflussen, aber niemals
allein eines erzeugen.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.sentiment.base import SentimentReading, SentimentSource

logger = get_logger(__name__)


class SentimentService:
    """Gewichteter Mittelwert aller verfuegbaren Sentiment-Quellen."""

    def __init__(
        self, sources: list[SentimentSource] | None = None, settings: Settings | None = None
    ) -> None:
        self._settings = settings or get_settings()
        self._sources = sources or []

    @property
    def is_enabled(self) -> bool:
        return self._settings.enable_sentiment and bool(self._sources)

    def register(self, source: SentimentSource) -> None:
        self._sources.append(source)

    async def get_readings(self, symbol: str) -> list[SentimentReading]:
        """Alle Quellen abfragen. Ausfaelle einzelner Quellen werden uebersprungen."""
        if not self.is_enabled:
            return []

        readings: list[SentimentReading] = []
        for source in self._sources:
            try:
                reading = await source.fetch(symbol)
            except Exception as exc:
                logger.warning(
                    "sentiment_source_failed", source=source.name, symbol=symbol, error=str(exc)
                )
                continue
            if reading is not None:
                readings.append(reading)
        return readings

    async def get_score(self, symbol: str) -> float | None:
        """Aggregierter Wert in [-100, +100], oder ``None`` ohne verlaessliche Daten."""
        readings = await self.get_readings(symbol)
        if not readings:
            return None

        total_weight = sum(reading.reliability for reading in readings)
        if total_weight <= 0:
            return None

        weighted = sum(reading.value * reading.reliability for reading in readings)
        score = weighted / total_weight
        return max(-100.0, min(100.0, score))

    async def close(self) -> None:
        for source in self._sources:
            try:
                await source.close()
            except Exception as exc:
                logger.debug("sentiment_source_close_failed", source=source.name, error=str(exc))
