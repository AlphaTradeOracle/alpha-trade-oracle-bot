"""Open Interest analyzer — architecture stub for later OI feeds."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.market.types import AnalyzerResult, MarketBias


class OpenInterestAnalyzer:
    name = "open_interest"

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
            detail=f"open_interest_feed_not_configured symbol={symbol or 'n/a'}",
            metrics={
                "symbol": symbol,
                "oi": None,
                "oiChange": None,
                "regime": None,
                # price_up_oi_up | price_up_oi_down | price_down_oi_up | price_down_oi_down
            },
        )
