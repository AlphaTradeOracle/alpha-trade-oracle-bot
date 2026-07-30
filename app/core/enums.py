"""Domaenen-Enums. Werte werden als Strings persistiert."""

from __future__ import annotations

from enum import StrEnum


class SignalDirection(StrEnum):
    STRONG_LONG = "STRONG_LONG"
    LONG = "LONG"
    NEUTRAL = "NEUTRAL"
    SHORT = "SHORT"
    STRONG_SHORT = "STRONG_SHORT"
    NO_TRADE = "NO_TRADE"

    @property
    def is_actionable(self) -> bool:
        """Nur diese Richtungen werden ueberhaupt versendet."""
        return self in {
            SignalDirection.STRONG_LONG,
            SignalDirection.LONG,
            SignalDirection.SHORT,
            SignalDirection.STRONG_SHORT,
        }

    @property
    def is_long(self) -> bool:
        return self in {SignalDirection.STRONG_LONG, SignalDirection.LONG}

    @property
    def is_short(self) -> bool:
        return self in {SignalDirection.STRONG_SHORT, SignalDirection.SHORT}


class Confidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TrendDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class MarketPhase(StrEnum):
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGE = "RANGE"
    VOLATILE = "VOLATILE"


class StructureState(StrEnum):
    HH_HL = "HH_HL"
    LH_LL = "LH_LL"
    RANGE = "RANGE"


class ScoreCategory(StrEnum):
    TREND = "trend"
    MOMENTUM = "momentum"
    VOLUME = "volume"
    VOLATILITY = "volatility"
    MARKET_STRUCTURE = "market_structure"
    MULTI_TIMEFRAME = "multi_timeframe"
    SENTIMENT = "sentiment"
    RISK_REWARD = "risk_reward"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class SuppressionReason(StrEnum):
    COOLDOWN = "cooldown"
    DUPLICATE = "duplicate"
    BELOW_MIN_SCORE = "below_min_score"
    NOT_STRONG = "not_strong"
    NOT_ACTIONABLE = "not_actionable"
    EXPIRED = "expired"
    LOW_DATA_QUALITY = "low_data_quality"
    RISK_REWARD_TOO_LOW = "risk_reward_too_low"
    NOTIFICATIONS_DISABLED = "notifications_disabled"


class BacktestStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExitReason(StrEnum):
    TAKE_PROFIT_1 = "take_profit_1"
    TAKE_PROFIT_2 = "take_profit_2"
    TAKE_PROFIT_3 = "take_profit_3"
    STOP_LOSS = "stop_loss"
    EXPIRED = "expired"
    END_OF_DATA = "end_of_data"
    #: Pending Retest-Entry nie gefuellt (Ablauf / SL vor Fill / keine Historie).
    RETEST_SKIPPED = "retest_skipped"


class LLMRequestStatus(StrEnum):
    SUCCESS = "success"
    RETRY_SUCCESS = "retry_success"
    VALIDATION_FAILED = "validation_failed"
    ERROR = "error"
    #: LLM war absichtlich deaktiviert. Getrennt von ERROR, damit ein bewusster
    #: Betrieb ohne LLM die Fehlerstatistik nicht verfaelscht.
    SKIPPED = "skipped"


class EventSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
