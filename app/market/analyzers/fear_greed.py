"""Crypto Fear & Greed Index analyzer (stub)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.market.types import AnalyzerResult, MarketBias


class FearGreedAnalyzer:
    name = "fear_greed"

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
            detail="fear_greed_feed_not_configured",
            metrics={
                "value": None,
                "label": None,  # extreme_fear|fear|neutral|greed|extreme_greed
            },
        )
