"""Shared types for institutional intelligence engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.enums import MarketPhase
from app.market_regime.types import MarketBias, MarketRegimeSnapshot


class InstitutionalPhase(StrEnum):
    """Extended market phases (KB Part 1). Maps onto legacy MarketPhase where possible."""

    TRENDING_BULLISH = "trending_bullish"
    TRENDING_BEARISH = "trending_bearish"
    RANGE = "range"
    EXPANSION = "expansion"
    COMPRESSION = "compression"
    ACCUMULATION = "accumulation"
    DISTRIBUTION = "distribution"
    RECOVERY = "recovery"
    CAPITULATION = "capitulation"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    UNCERTAIN = "uncertain"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class MarketNarrative(StrEnum):
    INSTITUTIONAL_ACCUMULATION = "institutional_accumulation"
    INSTITUTIONAL_DISTRIBUTION = "institutional_distribution"
    RETAIL_FOMO = "retail_fomo"
    RETAIL_PANIC = "retail_panic"
    SHORT_SQUEEZE = "short_squeeze"
    LONG_SQUEEZE = "long_squeeze"
    LIQUIDATION_CASCADE = "liquidation_cascade"
    SPOT_DRIVEN_RALLY = "spot_driven_rally"
    SPOT_DRIVEN_SELLOFF = "spot_driven_selloff"
    FUTURES_DRIVEN_RALLY = "futures_driven_rally"
    FUTURES_DRIVEN_SELLOFF = "futures_driven_selloff"
    MACRO_RISK_ON = "macro_risk_on"
    MACRO_RISK_OFF = "macro_risk_off"
    ALTCOIN_SEASON = "altcoin_season"
    BITCOIN_SEASON = "bitcoin_season"
    LIQUIDITY_COLLECTION = "liquidity_collection"
    TREND_CONTINUATION = "trend_continuation"
    TREND_EXHAUSTION = "trend_exhaustion"
    RANGE_EXPANSION = "range_expansion"
    RANGE_COMPRESSION = "range_compression"
    CAPITAL_ROTATION = "capital_rotation"
    UNCERTAIN = "uncertain"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class TradeDecisionLabel(StrEnum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    WEAK_BUY = "weak_buy"
    WATCHLIST = "watchlist"
    NO_TRADE = "no_trade"
    WEAK_SELL = "weak_sell"
    SELL = "sell"
    STRONG_SELL = "strong_sell"


@dataclass(frozen=True)
class PhaseSnapshot:
    phase: InstitutionalPhase
    confidence: float
    strength: float
    expected_behaviour: str
    legacy_phase: MarketPhase | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "label": self.phase.label,
            "confidence": round(self.confidence, 2),
            "strength": round(self.strength, 2),
            "expectedBehaviour": self.expected_behaviour,
            "legacyPhase": self.legacy_phase.value if self.legacy_phase else None,
        }


@dataclass(frozen=True)
class NarrativeSnapshot:
    primary: MarketNarrative
    secondary: tuple[MarketNarrative, ...]
    primary_driver: str
    institutional_participation: float
    capital_flow: str
    market_health: str
    continuation_probability: float
    reversal_probability: float
    confidence: float
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary.value,
            "primaryLabel": self.primary.label,
            "secondary": [n.value for n in self.secondary],
            "primaryDriver": self.primary_driver,
            "institutionalParticipation": round(self.institutional_participation, 2),
            "capitalFlow": self.capital_flow,
            "marketHealth": self.market_health,
            "continuationProbability": round(self.continuation_probability, 2),
            "reversalProbability": round(self.reversal_probability, 2),
            "confidence": round(self.confidence, 2),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class StructureContextSnapshot:
    trend: str
    structure_label: str
    bos: str | None
    choch: str | None
    liquidity_status: str
    volume_confirmed: bool
    structure_score: float
    confidence: float
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "trend": self.trend,
            "structure": self.structure_label,
            "bos": self.bos,
            "choch": self.choch,
            "liquidityStatus": self.liquidity_status,
            "volumeConfirmed": self.volume_confirmed,
            "structureScore": round(self.structure_score, 2),
            "confidence": round(self.confidence, 2),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class DataQualitySnapshot:
    quality_score: float
    reliability_score: float
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    unavailable: tuple[str, ...]
    confidence_adjustment: float
    trade_restricted: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualityScore": round(self.quality_score, 2),
            "reliabilityScore": round(self.reliability_score, 2),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "unavailable": list(self.unavailable),
            "confidenceAdjustment": round(self.confidence_adjustment, 2),
            "tradeRestricted": self.trade_restricted,
        }


@dataclass(frozen=True)
class ProbabilitySnapshot:
    win_probability: float
    loss_probability: float
    expected_value: float
    continuation_probability: float
    reversal_probability: float
    breakout_success: float
    fake_breakout: float
    liquidity_sweep: float
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "winProbability": round(self.win_probability, 4),
            "lossProbability": round(self.loss_probability, 4),
            "expectedValue": round(self.expected_value, 4),
            "continuationProbability": round(self.continuation_probability, 4),
            "reversalProbability": round(self.reversal_probability, 4),
            "breakoutSuccess": round(self.breakout_success, 4),
            "fakeBreakout": round(self.fake_breakout, 4),
            "liquiditySweep": round(self.liquidity_sweep, 4),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class GapAnalysisSnapshot:
    gap_score: float
    improvement_score: float
    missing_confirmations: tuple[str, ...]
    blocking_factors: tuple[str, ...]
    negative_factors: tuple[str, ...]
    positive_factors: tuple[str, ...]
    recommendations: tuple[str, ...]
    what_if: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "gapScore": round(self.gap_score, 2),
            "improvementScore": round(self.improvement_score, 2),
            "missingConfirmations": list(self.missing_confirmations),
            "blockingFactors": list(self.blocking_factors),
            "negativeFactors": list(self.negative_factors),
            "positiveFactors": list(self.positive_factors),
            "recommendations": list(self.recommendations),
            "whatIf": list(self.what_if),
        }


@dataclass(frozen=True)
class AdaptiveSnapshot:
    performance_score: float
    historical_confidence: float
    recommended_strategy: str
    recommended_risk_mult: float
    confidence_adjustment: float
    robustness_score: float
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "performanceScore": round(self.performance_score, 2),
            "historicalConfidence": round(self.historical_confidence, 2),
            "recommendedStrategy": self.recommended_strategy,
            "recommendedRiskMult": round(self.recommended_risk_mult, 3),
            "confidenceAdjustment": round(self.confidence_adjustment, 2),
            "robustnessScore": round(self.robustness_score, 2),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class HistoricalPatternSnapshot:
    historical_confidence: float
    similarity_score: float
    historical_edge: float
    pattern_class: str
    sample_size: int
    win_rate: float | None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "historicalConfidence": round(self.historical_confidence, 2),
            "similarityScore": round(self.similarity_score, 2),
            "historicalEdge": round(self.historical_edge, 4),
            "patternClass": self.pattern_class,
            "sampleSize": self.sample_size,
            "winRate": None if self.win_rate is None else round(self.win_rate, 4),
            "notes": list(self.notes),
        }


@dataclass
class InstitutionalContext:
    """Full market-intelligence + structure context before coin analysis."""

    completed: bool = False
    phase: PhaseSnapshot | None = None
    narrative: NarrativeSnapshot | None = None
    structure: StructureContextSnapshot | None = None
    data_quality: DataQualitySnapshot | None = None
    market_regime: MarketRegimeSnapshot | None = None
    adaptive: AdaptiveSnapshot | None = None
    historical: HistoricalPatternSnapshot | None = None
    pipeline_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    natural_language: str = ""

    @property
    def bias(self) -> MarketBias | None:
        if self.market_regime is None:
            return None
        return self.market_regime.bias

    def to_desk_dict(self) -> dict[str, Any]:
        return {
            "completed": self.completed,
            "phase": None if self.phase is None else self.phase.to_dict(),
            "narrative": None if self.narrative is None else self.narrative.to_dict(),
            "structure": None if self.structure is None else self.structure.to_dict(),
            "dataQuality": None if self.data_quality is None else self.data_quality.to_dict(),
            "marketRegime": None if self.market_regime is None else self.market_regime.to_desk_dict(),
            "adaptive": None if self.adaptive is None else self.adaptive.to_dict(),
            "historical": None if self.historical is None else self.historical.to_dict(),
            "pipelineSteps": list(self.pipeline_steps),
            "warnings": list(self.warnings),
            "summary": self.natural_language,
        }


@dataclass
class TradeExplainability:
    """Per-trade explainability payload (KB Parts 5/9)."""

    trade_score: float
    confidence_pct: float
    decision: TradeDecisionLabel
    expected_value: float | None = None
    win_probability: float | None = None
    loss_probability: float | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_factors: list[str] = field(default_factory=list)
    rejected_factors: list[str] = field(default_factory=list)
    engine_scores: dict[str, float] = field(default_factory=dict)
    gap: GapAnalysisSnapshot | None = None
    probability: ProbabilitySnapshot | None = None
    natural_language: str = ""
    no_trade_gates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tradeScore": round(self.trade_score, 2),
            "confidencePct": round(self.confidence_pct, 2),
            "decision": self.decision.value,
            "expectedValue": None
            if self.expected_value is None
            else round(self.expected_value, 4),
            "winProbability": None
            if self.win_probability is None
            else round(self.win_probability, 4),
            "lossProbability": None
            if self.loss_probability is None
            else round(self.loss_probability, 4),
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "missingFactors": list(self.missing_factors),
            "rejectedFactors": list(self.rejected_factors),
            "engineScores": {k: round(v, 2) for k, v in self.engine_scores.items()},
            "gap": None if self.gap is None else self.gap.to_dict(),
            "probability": None if self.probability is None else self.probability.to_dict(),
            "summary": self.natural_language,
            "noTradeGates": list(self.no_trade_gates),
        }

    def to_json_safe(self) -> dict[str, Any]:
        return self.to_dict()


def clamp_score(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))
