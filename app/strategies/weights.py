"""Strategiegewichte. Die Summe muss immer exakt 1.0 ergeben."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.core.enums import ScoreCategory

#: Toleranz fuer die Gewichtssumme — Float-Arithmetik erlaubt keine exakte 1.0.
WEIGHT_SUM_TOLERANCE = 1e-6


class StrategyWeights(BaseModel):
    """Gewichtung der Score-Kategorien.

    Zurueck auf v1 (pre-18/18): Structure 16.38%, MTF 10.46%. ``sentiment`` bleibt 0.
    Die 18/18-Variante (v2) lag in der Counterfactual-Sim unter Baseline.

    Die Klasse ist unveraenderlich. Eine geaenderte Gewichtung ist immer eine
    neue Instanz und wird als neue Strategieversion persistiert — nie als
    Ueberschreiben einer bestehenden.
    """

    model_config = {"frozen": True}

    trend: float = Field(default=0.2730, ge=0.0, le=1.0)
    momentum: float = Field(default=0.2184, ge=0.0, le=1.0)
    volume: float = Field(default=0.1638, ge=0.0, le=1.0)
    market_structure: float = Field(default=0.1638, ge=0.0, le=1.0)
    multi_timeframe: float = Field(default=0.1046, ge=0.0, le=1.0)
    volatility: float = Field(default=0.0437, ge=0.0, le=1.0)
    sentiment: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_reward: float = Field(default=0.0327, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_sum(self) -> StrategyWeights:
        total = self.total()
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"Die Summe der Gewichte muss 1.0 ergeben, betraegt aber {total:.6f}. "
                f"Einzelwerte: {self.as_dict()}"
            )
        return self

    def total(self) -> float:
        return (
            self.trend
            + self.momentum
            + self.volume
            + self.market_structure
            + self.multi_timeframe
            + self.volatility
            + self.sentiment
            + self.risk_reward
        )

    def as_dict(self) -> dict[ScoreCategory, float]:
        return {
            ScoreCategory.TREND: self.trend,
            ScoreCategory.MOMENTUM: self.momentum,
            ScoreCategory.VOLUME: self.volume,
            ScoreCategory.MARKET_STRUCTURE: self.market_structure,
            ScoreCategory.MULTI_TIMEFRAME: self.multi_timeframe,
            ScoreCategory.VOLATILITY: self.volatility,
            ScoreCategory.SENTIMENT: self.sentiment,
            ScoreCategory.RISK_REWARD: self.risk_reward,
        }

    def without_sentiment(self) -> StrategyWeights:
        """Sentiment-Gewicht proportional auf die anderen Kategorien verteilen.

        Wird verwendet, wenn ``ENABLE_SENTIMENT=false`` ist. So bleibt die Summe
        1.0, ohne dass ein neutraler Sentiment-Wert den Score verwaessert.
        """
        if self.sentiment == 0.0:
            return self

        remaining = self.total() - self.sentiment
        if remaining <= 0:
            raise ValueError("Ohne Sentiment bleibt kein Gewicht uebrig")

        factor = 1.0 / remaining
        return StrategyWeights(
            trend=self.trend * factor,
            momentum=self.momentum * factor,
            volume=self.volume * factor,
            market_structure=self.market_structure * factor,
            multi_timeframe=self.multi_timeframe * factor,
            volatility=self.volatility * factor,
            sentiment=0.0,
            risk_reward=self.risk_reward * factor,
        )

    def to_db_columns(self) -> dict[str, float]:
        """Spaltennamen der Tabelle ``strategy_versions``."""
        return {
            "trend_weight": self.trend,
            "momentum_weight": self.momentum,
            "volume_weight": self.volume,
            "market_structure_weight": self.market_structure,
            "multi_timeframe_weight": self.multi_timeframe,
            "volatility_weight": self.volatility,
            "sentiment_weight": self.sentiment,
            "risk_reward_weight": self.risk_reward,
        }

    @classmethod
    def from_db_columns(cls, row: Any) -> StrategyWeights:
        return cls(
            trend=float(row.trend_weight),
            momentum=float(row.momentum_weight),
            volume=float(row.volume_weight),
            market_structure=float(row.market_structure_weight),
            multi_timeframe=float(row.multi_timeframe_weight),
            volatility=float(row.volatility_weight),
            sentiment=float(row.sentiment_weight),
            risk_reward=float(row.risk_reward_weight),
        )


#: Standardgewichtung des MVP.
DEFAULT_WEIGHTS = StrategyWeights()

#: Rollen der Timeframes in der Multi-Timeframe-Bewertung. Summe der Werte = 1.0.
TIMEFRAME_ROLE_WEIGHTS: dict[str, float] = {
    "1d": 0.35,
    "4h": 0.30,
    "1h": 0.25,
    "15m": 0.10,
}
