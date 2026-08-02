"""Shared types for the global market-regime stack."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class MarketBias(StrEnum):
    STRONG_BULLISH = "strong_bullish"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    STRONG_BEARISH = "strong_bearish"


BIAS_TO_SIGNED: dict[MarketBias, float] = {
    MarketBias.STRONG_BULLISH: 80.0,
    MarketBias.BULLISH: 45.0,
    MarketBias.NEUTRAL: 0.0,
    MarketBias.BEARISH: -45.0,
    MarketBias.STRONG_BEARISH: -80.0,
}


def bias_from_signed(score: float) -> MarketBias:
    if score >= 60:
        return MarketBias.STRONG_BULLISH
    if score >= 25:
        return MarketBias.BULLISH
    if score <= -60:
        return MarketBias.STRONG_BEARISH
    if score <= -25:
        return MarketBias.BEARISH
    return MarketBias.NEUTRAL


@dataclass(frozen=True)
class AnalyzerResult:
    """Output of a single market analyzer module."""

    name: str
    available: bool
    #: Directional market lean in [-100, +100] (positive = risk-on / bullish).
    score: float
    bias: MarketBias = MarketBias.NEUTRAL
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketContext:
    """Full market snapshot at decision time — persisted with signals/trades."""

    asof: datetime
    bias: MarketBias
    #: Aggregate signed market score [-100, +100]
    market_score: float
    available: bool
    detail: str
    components: dict[str, AnalyzerResult] = field(default_factory=dict)
    #: Ready for desk / paper notes
    def to_dict(self) -> dict[str, Any]:
        return {
            "asof": self.asof.isoformat(),
            "bias": self.bias.value,
            "marketScore": round(self.market_score, 2),
            "available": self.available,
            "detail": self.detail,
            "components": {
                name: {
                    "available": r.available,
                    "score": round(r.score, 2),
                    "bias": r.bias.value,
                    "detail": r.detail,
                    "metrics": r.metrics,
                }
                for name, r in self.components.items()
            },
        }


@dataclass(frozen=True)
class ScoreBlendWeights:
    coin: float = 0.60
    market: float = 0.25
    funding: float = 0.05
    open_interest: float = 0.05
    liquidations: float = 0.05

    def normalized(self) -> ScoreBlendWeights:
        total = self.coin + self.market + self.funding + self.open_interest + self.liquidations
        if total <= 0:
            return ScoreBlendWeights(1.0, 0.0, 0.0, 0.0, 0.0)
        return ScoreBlendWeights(
            coin=self.coin / total,
            market=self.market / total,
            funding=self.funding / total,
            open_interest=self.open_interest / total,
            liquidations=self.liquidations / total,
        )


@dataclass(frozen=True)
class BlendedScore:
    coin_score: float
    #: Market lean on the same 0..100 bipolar scale as the coin score.
    market_component: float
    funding_component: float
    open_interest_component: float
    liquidation_component: float
    final_score: float
    overall_bias: MarketBias
    weights: ScoreBlendWeights
