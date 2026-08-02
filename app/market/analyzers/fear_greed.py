"""Crypto Fear & Greed Index analyzer (alternative.me)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.market.types import AnalyzerResult, MarketBias, bias_from_signed


def _label_from_value(value: int) -> str:
    if value <= 24:
        return "extreme_fear"
    if value <= 44:
        return "fear"
    if value <= 55:
        return "neutral"
    if value <= 74:
        return "greed"
    return "extreme_greed"


def score_from_fear_greed(value: int) -> float:
    """Mildly contrarian lean: extreme fear → bounce bias, extreme greed → caution."""
    if value <= 10:
        return 55.0
    if value <= 24:
        return 40.0
    if value <= 44:
        return 15.0
    if value <= 55:
        return 0.0
    if value <= 74:
        return -15.0
    if value <= 90:
        return -40.0
    return -55.0


class FearGreedAnalyzer:
    name = "fear_greed"

    def analyze(
        self,
        *,
        asof: datetime,
        frames: dict[str, Any] | None = None,
        symbol: str | None = None,
    ) -> AnalyzerResult:
        del asof, symbol
        payload = None
        if isinstance(frames, dict):
            payload = frames.get("payload") or frames.get("fear_greed")
        if not payload:
            return AnalyzerResult(
                name=self.name,
                available=False,
                score=0.0,
                bias=MarketBias.NEUTRAL,
                detail="fear_greed_unavailable",
                metrics={"value": None, "label": None},
            )

        value = int(payload["value"])
        label = str(payload.get("label") or _label_from_value(value))
        score = score_from_fear_greed(value)
        return AnalyzerResult(
            name=self.name,
            available=True,
            score=round(score, 2),
            bias=bias_from_signed(score),
            detail=f"F&G {value} ({label}) lean={score:+.0f}",
            metrics={
                "value": value,
                "label": label,
                "classification": payload.get("classification"),
                "timestamp": payload.get("timestamp"),
            },
        )
