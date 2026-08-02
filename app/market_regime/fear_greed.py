"""Crypto Fear & Greed Index analyzer."""

from __future__ import annotations

from app.market_regime.sources import FearGreedClient
from app.market_regime.types import FearGreedAnalysis, FearGreedBand


class FearGreedAnalyzer:
    def __init__(self, client: FearGreedClient | None = None) -> None:
        self._client = client

    async def analyze(self) -> FearGreedAnalysis:
        if self._client is None:
            return FearGreedAnalysis(available=False, detail="fear_greed_client_missing")
        reading = await self._client.fetch()
        if reading is None:
            return FearGreedAnalysis(available=False, detail="fear_greed_unavailable")

        band = _band(reading.value)
        # Contrarian tilt: extreme fear can support longs; extreme greed caution.
        score = {
            FearGreedBand.EXTREME_FEAR: 40.0,
            FearGreedBand.FEAR: 20.0,
            FearGreedBand.NEUTRAL: 0.0,
            FearGreedBand.GREED: -15.0,
            FearGreedBand.EXTREME_GREED: -40.0,
        }[band]
        return FearGreedAnalysis(
            available=True,
            value=reading.value,
            band=band,
            score=score,
            detail=f"fng={reading.value} band={band.value}",
        )


def _band(value: int) -> FearGreedBand:
    if value <= 20:
        return FearGreedBand.EXTREME_FEAR
    if value <= 40:
        return FearGreedBand.FEAR
    if value <= 60:
        return FearGreedBand.NEUTRAL
    if value <= 80:
        return FearGreedBand.GREED
    return FearGreedBand.EXTREME_GREED
