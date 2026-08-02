"""Datentypen der Signal-Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.core.enums import (
    Confidence,
    MarketPhase,
    ScoreCategory,
    SignalDirection,
)
from app.indicators.engine import IndicatorSet


@dataclass(frozen=True)
class ScoreComponent:
    """Ergebnis einer einzelnen Score-Kategorie."""

    category: ScoreCategory
    #: Rohwert in [-100, +100]; positiv = bullisch.
    raw_score: float
    weight: float
    detail: str = ""

    @property
    def weighted_score(self) -> float:
        return self.raw_score * self.weight


@dataclass
class RiskParameters:
    """Ergebnis der Risikoberechnung. Rein informativ, keine Orderdaten."""

    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    risk_reward_ratio: float
    risk_percent: float
    suggested_position_size: float
    stop_distance_percent: float
    invalidation_note: str
    warnings: list[str] = field(default_factory=list)

    @property
    def entry_mid(self) -> float:
        return (self.entry_low + self.entry_high) / 2.0


@dataclass
class TimeframeAssessment:
    """Bewertung eines einzelnen Timeframes."""

    timeframe: str
    role_weight: float
    indicators: IndicatorSet
    trend_score: float
    momentum_score: float
    volume_score: float
    volatility_score: float
    structure_score: float
    notes: list[str] = field(default_factory=list)

    @property
    def directional_score(self) -> float:
        """Zusammengefasste Richtungstendenz dieses Timeframes in [-100, +100]."""
        return max(
            -100.0,
            min(
                100.0,
                self.trend_score * 0.40
                + self.momentum_score * 0.25
                + self.structure_score * 0.20
                + self.volume_score * 0.15,
            ),
        )


@dataclass
class SignalResult:
    """Vollstaendiges Analyseergebnis eines Symbols."""

    symbol: str
    created_at: datetime
    expires_at: datetime
    direction: SignalDirection
    score: float
    confidence: Confidence
    market_phase: MarketPhase
    primary_timeframe: str
    analyzed_timeframes: list[str]
    reference_price: float
    data_quality: float
    components: list[ScoreComponent]
    assessments: dict[str, TimeframeAssessment]
    risk: RiskParameters | None
    reasons: list[str] = field(default_factory=list)
    counter_arguments: list[str] = field(default_factory=list)
    indicators_used: list[str] = field(default_factory=list)
    fingerprint: str = ""
    llm_summary: str | None = None
    #: Falls die Engine NO_TRADE gesetzt hat: der Grund dafuer.
    no_trade_reason: str | None = None
    #: Coin-only score before Market Regime blend (0..100).
    coin_score: float | None = None
    #: Market context snapshot at signal time (desk / paper persistence).
    market_context: dict | None = None
    #: Numeric confidence 0–100 (distinct from categorical Confidence).
    confidence_pct: float | None = None
    #: Institutional explainability (KB Parts 5/9).
    explainability: dict | None = None

    @property
    def is_actionable(self) -> bool:
        return self.direction.is_actionable

    @property
    def multi_timeframe_agreement(self) -> float:
        """Uebereinstimmung der Timeframes in [-1, +1]."""
        component = self.component(ScoreCategory.MULTI_TIMEFRAME)
        return component.raw_score / 100.0 if component else 0.0

    def component(self, category: ScoreCategory) -> ScoreComponent | None:
        return next((c for c in self.components if c.category == category), None)

    def score_breakdown(self) -> dict[str, dict[str, float | str]]:
        return {
            component.category.value: {
                "raw_score": round(component.raw_score, 2),
                "weight": round(component.weight, 4),
                "weighted_score": round(component.weighted_score, 4),
                "detail": component.detail,
            }
            for component in self.components
        }
