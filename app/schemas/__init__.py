"""Request- und Response-Schemas der API."""

from app.schemas.backtest import BacktestRequest, BacktestResponse, BacktestTradeResponse
from app.schemas.common import (
    DISCLAIMER_TEXT,
    AssetResponse,
    ErrorResponse,
    HealthResponse,
    ReadinessResponse,
    VersionResponse,
)
from app.schemas.signal import (
    AnalysisRequest,
    AnalysisResponse,
    PerformanceResponse,
    RiskResponse,
    ScoreComponentResponse,
    SignalResponse,
)

__all__ = [
    "DISCLAIMER_TEXT",
    "AnalysisRequest",
    "AnalysisResponse",
    "AssetResponse",
    "BacktestRequest",
    "BacktestResponse",
    "BacktestTradeResponse",
    "ErrorResponse",
    "HealthResponse",
    "PerformanceResponse",
    "ReadinessResponse",
    "RiskResponse",
    "ScoreComponentResponse",
    "SignalResponse",
    "VersionResponse",
]
