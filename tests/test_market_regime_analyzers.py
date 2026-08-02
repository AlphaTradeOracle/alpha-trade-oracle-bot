"""Unit tests for funding / fear-greed / dominance / OI / liquidation analyzers."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from app.market_regime.dominance import DominanceAnalyzer
from app.market_regime.fear_greed import FearGreedAnalyzer
from app.market_regime.funding import FundingAnalyzer
from app.market_regime.liquidations import LiquidationAnalyzer
from app.market_regime.open_interest import OpenInterestAnalyzer
from app.market_regime.sources import (
    DominanceReading,
    FearGreedReading,
    FundingReading,
    OpenInterestReading,
)
from app.market_regime.types import (
    DominanceTrend,
    FearGreedBand,
    FundingStatus,
    OiPriceRelation,
    RiskMode,
)


class _FundingClient:
    def __init__(self, readings: dict[str, FundingReading]) -> None:
        self._readings = readings

    async def fetch_funding(self, symbol: str, *, history_limit: int = 24) -> FundingReading | None:
        return self._readings.get(symbol.upper())


class _FearClient:
    def __init__(self, reading: FearGreedReading | None) -> None:
        self._reading = reading

    async def fetch(self) -> FearGreedReading | None:
        return self._reading


class _DomClient:
    def __init__(self, reading: DominanceReading | None) -> None:
        self._reading = reading

    async def fetch(self) -> DominanceReading | None:
        return self._reading


class _OiClient:
    def __init__(self, readings: dict[str, OpenInterestReading]) -> None:
        self._readings = readings

    async def fetch_open_interest(
        self, symbol: str, *, hist_limit: int = 12
    ) -> OpenInterestReading | None:
        return self._readings.get(symbol.upper())


@pytest.mark.asyncio
async def test_funding_very_positive_is_long_cautious() -> None:
    client = _FundingClient(
        {
            "ETHUSDT": FundingReading(
                "ETHUSDT", 0.001, 3000.0, None, (0.0002, 0.0005, 0.001)
            ),
            "BTCUSDT": FundingReading(
                "BTCUSDT", 0.0008, 60000.0, None, (0.0003, 0.0006, 0.0008)
            ),
        }
    )
    result = await FundingAnalyzer(client).analyze("ETHUSDT")  # type: ignore[arg-type]
    assert result.available
    assert result.status is FundingStatus.VERY_POSITIVE
    assert result.score < 0


@pytest.mark.asyncio
async def test_fear_greed_extreme_fear_supports_longs() -> None:
    client = _FearClient(
        FearGreedReading(12, "Extreme Fear", datetime(2024, 6, 1, tzinfo=UTC))
    )
    result = await FearGreedAnalyzer(client).analyze()  # type: ignore[arg-type]
    assert result.available
    assert result.band is FearGreedBand.EXTREME_FEAR
    assert result.score > 0


@pytest.mark.asyncio
async def test_dominance_risk_on_when_usdt_falls() -> None:
    import app.market_regime.dominance as dominance_mod

    dominance_mod._PREV = None
    # Seed previous reading via first call, then falling USDT / BTC.D.
    client = _DomClient(
        DominanceReading(52.0, 15.0, 6.0, 2_000_000_000_000.0, 600_000_000_000.0)
    )
    analyzer = DominanceAnalyzer(client)  # type: ignore[arg-type]
    first = await analyzer.analyze()
    assert first.available

    client._reading = DominanceReading(
        50.5, 16.0, 5.5, 2_100_000_000_000.0, 700_000_000_000.0
    )
    second = await analyzer.analyze()
    assert second.btc_dominance_trend is DominanceTrend.FALLING
    assert second.usdt_risk_mode is RiskMode.RISK_ON
    assert second.score > 0


@pytest.mark.asyncio
async def test_open_interest_price_up_oi_up() -> None:
    client = _OiClient(
        {
            "ETHUSDT": OpenInterestReading("ETHUSDT", 1_200.0, (1000.0, 1100.0, 1200.0)),
            "BTCUSDT": OpenInterestReading("BTCUSDT", 50_000.0, (49_000.0, 50_000.0)),
        }
    )
    result = await OpenInterestAnalyzer(client).analyze(  # type: ignore[arg-type]
        "ETHUSDT", price_change_pct=1.5
    )
    assert result.available
    assert result.relation is OiPriceRelation.PRICE_UP_OI_UP
    assert result.score > 0


@pytest.mark.asyncio
async def test_liquidation_heuristic_available() -> None:
    idx = pd.date_range("2024-01-01", periods=40, freq="h", tz="UTC")
    close = [float(100 + i) for i in range(40)]
    frame = pd.DataFrame(
        {
            "open": close,
            "high": [c + 1 for c in close],
            "low": [c - 1 for c in close[:-1]] + [close[-1] - 8],
            "close": close,
            "volume": [1000.0] * 40,
        },
        index=idx,
    )
    result = await LiquidationAnalyzer().analyze(btc_frame=frame)
    assert result.available
    assert result.source in {"wick_heuristic", "free_venues"}
    assert result.liquidity_score is not None or result.score != 0
