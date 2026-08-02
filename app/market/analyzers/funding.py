"""Per-coin + BTC funding-rate analyzer (interface ready; feed TBD)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.market.types import AnalyzerResult, MarketBias


class FundingAnalyzer:
    name = "funding"

    def analyze(
        self,
        *,
        asof: datetime,
        frames: dict[str, pd.DataFrame] | None = None,
        symbol: str | None = None,
    ) -> AnalyzerResult:
        del asof, frames
        # When funding series are available, map:
        # very positive → overheated longs → negative lean for longs (score < 0)
        # very negative → short-squeeze risk → positive lean for longs
        return AnalyzerResult(
            name=self.name,
            available=False,
            score=0.0,
            bias=MarketBias.NEUTRAL,
            detail=f"funding_feed_not_configured symbol={symbol or 'n/a'}",
            metrics={
                "symbol": symbol,
                "current": None,
                "average": None,
                "changeHours": None,
                "extreme": None,
                "btcFunding": None,
            },
        )
