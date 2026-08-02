"""Liquidation / heatmap analyzer — architecture stub."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.market.types import AnalyzerResult, MarketBias


class LiquidationAnalyzer:
    name = "liquidations"

    def analyze(
        self,
        *,
        asof: datetime,
        frames: dict[str, pd.DataFrame] | None = None,
        symbol: str | None = None,
    ) -> AnalyzerResult:
        del asof, frames
        return AnalyzerResult(
            name=self.name,
            available=False,
            score=0.0,
            bias=MarketBias.NEUTRAL,
            detail=f"liquidations_feed_not_configured symbol={symbol or 'n/a'}",
            metrics={
                "symbol": symbol,
                "longLiquidations": None,
                "shortLiquidations": None,
                "clusters": None,
            },
        )
