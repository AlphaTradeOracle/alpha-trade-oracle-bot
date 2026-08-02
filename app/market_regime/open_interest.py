"""Open Interest analyzer (Binance futures)."""

from __future__ import annotations

from app.market_regime.sources import DerivativesClient, OpenInterestReading
from app.market_regime.types import OiPriceRelation, OpenInterestAnalysis


class OpenInterestAnalyzer:
    def __init__(self, client: DerivativesClient | None = None) -> None:
        self._client = client

    async def analyze(
        self,
        symbol: str,
        *,
        btc_symbol: str = "BTCUSDT",
        price_change_pct: float | None = None,
    ) -> OpenInterestAnalysis:
        if self._client is None:
            return OpenInterestAnalysis(available=False, detail="oi_client_missing")

        symbol_oi = await self._client.fetch_open_interest(symbol)
        btc_oi = await self._client.fetch_open_interest(btc_symbol)
        if symbol_oi is None and btc_oi is None:
            return OpenInterestAnalysis(available=False, detail="oi_unavailable")

        change = _change_pct(symbol_oi) or _change_pct(btc_oi)
        relation = _relation(price_change_pct, change)
        score = _score(relation)
        return OpenInterestAnalysis(
            available=True,
            symbol_oi=symbol_oi.open_interest if symbol_oi else None,
            btc_oi=btc_oi.open_interest if btc_oi else None,
            symbol_oi_change_pct=None if change is None else round(change, 4),
            relation=relation,
            score=score,
            detail=f"oi relation={relation.value} change={change}",
        )


def _change_pct(reading: OpenInterestReading | None) -> float | None:
    if reading is None or len(reading.history) < 2:
        return None
    base = reading.history[0]
    if base <= 0:
        return None
    return (reading.history[-1] - base) / base * 100.0


def _relation(
    price_change_pct: float | None,
    oi_change_pct: float | None,
) -> OiPriceRelation:
    if price_change_pct is None or oi_change_pct is None:
        return OiPriceRelation.UNKNOWN
    price_up = price_change_pct > 0.15
    price_down = price_change_pct < -0.15
    oi_up = oi_change_pct > 0.5
    oi_down = oi_change_pct < -0.5
    if price_up and oi_up:
        return OiPriceRelation.PRICE_UP_OI_UP
    if price_up and oi_down:
        return OiPriceRelation.PRICE_UP_OI_DOWN
    if price_down and oi_up:
        return OiPriceRelation.PRICE_DOWN_OI_UP
    if price_down and oi_down:
        return OiPriceRelation.PRICE_DOWN_OI_DOWN
    return OiPriceRelation.FLAT


def _score(relation: OiPriceRelation) -> float:
    # Bullish-positive score: long buildup supports longs; short buildup (price down + oi up) supports shorts.
    return {
        OiPriceRelation.PRICE_UP_OI_UP: 40.0,  # long buildup
        OiPriceRelation.PRICE_UP_OI_DOWN: -20.0,  # short cover / weak rally
        OiPriceRelation.PRICE_DOWN_OI_UP: -40.0,  # short buildup
        OiPriceRelation.PRICE_DOWN_OI_DOWN: 15.0,  # long liquidation / capitulation
        OiPriceRelation.FLAT: 0.0,
        OiPriceRelation.UNKNOWN: 0.0,
    }[relation]
