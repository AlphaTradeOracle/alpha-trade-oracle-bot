"""Analyzer protocol — every market module implements this."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import pandas as pd

from app.market.types import AnalyzerResult


class MarketAnalyzer(Protocol):
    name: str

    def analyze(
        self,
        *,
        asof: datetime,
        frames: dict[str, pd.DataFrame] | None = None,
        symbol: str | None = None,
    ) -> AnalyzerResult:
        """Return a scored market lean. Missing data → available=False, score=0."""
        ...
