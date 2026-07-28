"""SQLAlchemy-Modelle. Der Import hier registriert alle Tabellen bei Alembic."""

from app.database.base import Base
from app.models.backtest import BacktestMetric, BacktestRun, BacktestTrade
from app.models.market import Asset, IndicatorSnapshot, MarketCandle
from app.models.operations import ApplicationEvent, ScheduledJob
from app.models.signal import LLMRequest, Signal, SignalDelivery, SignalScoreComponent
from app.models.strategy import ModelConfig, Strategy, StrategyVersion
from app.models.user import TelegramChat, User, Watchlist
from app.models.paper import PaperAccount, PaperFill, PaperPosition

__all__ = [
    "ApplicationEvent",
    "Asset",
    "BacktestMetric",
    "BacktestRun",
    "BacktestTrade",
    "Base",
    "IndicatorSnapshot",
    "LLMRequest",
    "MarketCandle",
    "ModelConfig",
    "PaperAccount",
    "PaperFill",
    "PaperPosition",
    "ScheduledJob",
    "Signal",
    "SignalDelivery",
    "SignalScoreComponent",
    "Strategy",
    "StrategyVersion",
    "TelegramChat",
    "User",
    "Watchlist",
]
