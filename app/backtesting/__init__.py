"""Backtesting: look-ahead-freie Simulation mit derselben Logik wie im Live-Betrieb."""

from app.backtesting.engine import (
    WARMUP_CANDLES,
    BacktestConfig,
    BacktestEngine,
    BacktestOutcome,
    SimulatedTrade,
)
from app.backtesting.metrics import compute_metrics, summarize_for_display
from app.backtesting.optimizer import (
    CalibrationReport,
    CandidateEvaluation,
    build_walk_forward_windows,
    evaluate_candidates,
    generate_weight_candidates,
)

__all__ = [
    "WARMUP_CANDLES",
    "BacktestConfig",
    "BacktestEngine",
    "BacktestOutcome",
    "CalibrationReport",
    "CandidateEvaluation",
    "SimulatedTrade",
    "build_walk_forward_windows",
    "compute_metrics",
    "evaluate_candidates",
    "generate_weight_candidates",
    "summarize_for_display",
]
