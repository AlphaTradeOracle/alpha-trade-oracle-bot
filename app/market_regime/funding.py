"""Funding-rate analyzer for the traded coin and BTC."""

from __future__ import annotations

from statistics import mean

from app.market_regime.sources import DerivativesClient, FundingReading
from app.market_regime.types import FundingAnalysis, FundingStatus


class FundingAnalyzer:
    #: Absolute funding thresholds (8h rate).
    VERY_POS = 0.0005  # 0.05%
    POS = 0.0001
    NEG = -0.0001
    VERY_NEG = -0.0005

    def __init__(self, client: DerivativesClient | None = None) -> None:
        self._client = client

    async def analyze(
        self,
        symbol: str,
        *,
        btc_symbol: str = "BTCUSDT",
    ) -> FundingAnalysis:
        if self._client is None:
            return FundingAnalysis(available=False, detail="funding_client_missing")

        symbol_reading = await self._client.fetch_funding(symbol)
        btc_reading = await self._client.fetch_funding(btc_symbol)
        if symbol_reading is None and btc_reading is None:
            return FundingAnalysis(available=False, detail="funding_unavailable")

        status, score = self._evaluate(symbol_reading, btc_reading)
        return FundingAnalysis(
            available=True,
            symbol_rate=symbol_reading.rate if symbol_reading else None,
            btc_rate=btc_reading.rate if btc_reading else None,
            symbol_avg=_avg(symbol_reading),
            btc_avg=_avg(btc_reading),
            symbol_change=_change(symbol_reading),
            status=status,
            score=score,
            detail=(
                f"funding status={status.value} "
                f"sym={symbol_reading.rate if symbol_reading else None} "
                f"btc={btc_reading.rate if btc_reading else None}"
            ),
        )

    def _evaluate(
        self,
        symbol: FundingReading | None,
        btc: FundingReading | None,
    ) -> tuple[FundingStatus, float]:
        # Prefer coin funding; blend with BTC when both exist.
        rates: list[float] = []
        if symbol is not None:
            rates.append(symbol.rate)
        if btc is not None:
            rates.append(btc.rate)
        blended = mean(rates) if rates else 0.0

        if blended >= self.VERY_POS:
            status = FundingStatus.VERY_POSITIVE
            # Overheated longs → negative for new longs (score is bullish-positive).
            score = -70.0
        elif blended >= self.POS:
            status = FundingStatus.POSITIVE
            score = -30.0
        elif blended <= self.VERY_NEG:
            status = FundingStatus.VERY_NEGATIVE
            score = 70.0  # short squeeze potential → long-friendly
        elif blended <= self.NEG:
            status = FundingStatus.NEGATIVE
            score = 30.0
        else:
            status = FundingStatus.NEUTRAL
            score = 0.0

        # Amplify if recent change accelerates extremes.
        change = _change(symbol) or _change(btc)
        if change is not None and abs(change) > 0.0002:
            score = max(-100.0, min(100.0, score + (-25.0 if change > 0 else 25.0)))
        return status, round(score, 2)


def _avg(reading: FundingReading | None) -> float | None:
    if reading is None or not reading.history:
        return None
    return float(mean(reading.history))


def _change(reading: FundingReading | None) -> float | None:
    if reading is None or len(reading.history) < 2:
        return None
    return float(reading.history[-1] - reading.history[0])
