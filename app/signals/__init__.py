"""Signal-Engine: Scoring, Multi-Timeframe, Risiko und Deduplizierung."""

from app.signals.dedup import DedupDecision, PreviousSignal, SignalDeduplicator
from app.signals.engine import SignalEngine, SignalEngineConfig
from app.signals.risk import RiskConfig, RiskManager
from app.signals.types import RiskParameters, ScoreComponent, SignalResult, TimeframeAssessment

__all__ = [
    "DedupDecision",
    "PreviousSignal",
    "RiskConfig",
    "RiskManager",
    "RiskParameters",
    "ScoreComponent",
    "SignalDeduplicator",
    "SignalEngine",
    "SignalEngineConfig",
    "SignalResult",
    "TimeframeAssessment",
]
