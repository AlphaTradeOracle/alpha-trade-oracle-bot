"""BTC.D / USDT.D / TOTAL3 dominance analyzers (stubs with stable interface)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.market.types import AnalyzerResult, MarketBias


class DominanceAnalyzer:
    """Placeholder until CoinGecko/TradingView dominance series are wired."""

    name = "dominance"

    def analyze(
        self,
        *,
        asof: datetime,
        frames: dict[str, pd.DataFrame] | None = None,
        symbol: str | None = None,
    ) -> AnalyzerResult:
        del asof, frames, symbol
        return AnalyzerResult(
            name=self.name,
            available=False,
            score=0.0,
            bias=MarketBias.NEUTRAL,
            detail="dominance_feed_not_configured (BTC.D / USDT.D / TOTAL3 ready)",
            metrics={
                "btcDominance": None,
                "btcDominanceTrend": None,  # rising | falling
                "usdtDominance": None,
                "usdtDominanceMode": None,  # risk_off | risk_on
                "total3": None,
                "total3Trend": None,
            },
        )
