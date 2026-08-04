"""Strategien, versionierte Gewichtungen und LLM-Modellkonfigurationen."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import JSON_COLUMN, RATIO, SCORE, WEIGHT, Base, TimestampMixin


class Strategy(Base, TimestampMixin):
    """Eine benannte Signalstrategie, z. B. 'default'."""

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    versions: Mapped[list[StrategyVersion]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )


class StrategyVersion(Base, TimestampMixin):
    """Ein konkreter, unveraenderlicher Gewichtungssatz.

    Neue Gewichte werden immer als neue Version angelegt, niemals in einer
    bestehenden Version ueberschrieben. Nur so bleibt nachvollziehbar, mit
    welchen Parametern ein historisches Signal entstanden ist.
    """

    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),
        # Die Summe aller Gewichte muss 1.0 ergeben. Die Datenbank ist hier die
        # letzte Verteidigungslinie; validiert wird zusaetzlich im Pydantic-Modell.
        CheckConstraint(
            "abs(trend_weight + momentum_weight + volume_weight + volatility_weight"
            " + market_structure_weight + multi_timeframe_weight + sentiment_weight"
            " + risk_reward_weight - 1.0) < 0.000001",
            name="weights_sum_to_one",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    trend_weight: Mapped[float] = mapped_column(WEIGHT, nullable=False)
    momentum_weight: Mapped[float] = mapped_column(WEIGHT, nullable=False)
    volume_weight: Mapped[float] = mapped_column(WEIGHT, nullable=False)
    volatility_weight: Mapped[float] = mapped_column(WEIGHT, nullable=False)
    market_structure_weight: Mapped[float] = mapped_column(WEIGHT, nullable=False)
    multi_timeframe_weight: Mapped[float] = mapped_column(WEIGHT, nullable=False)
    sentiment_weight: Mapped[float] = mapped_column(WEIGHT, nullable=False)
    risk_reward_weight: Mapped[float] = mapped_column(WEIGHT, nullable=False)

    min_score: Mapped[float] = mapped_column(SCORE, nullable=False, default=65.0)
    min_risk_reward_ratio: Mapped[float] = mapped_column(RATIO, nullable=False, default=2.0)
    atr_multiplier: Mapped[float] = mapped_column(RATIO, nullable=False, default=1.5)

    strategy: Mapped[Strategy] = relationship(back_populates="versions")

    def __repr__(self) -> str:
        return f"<StrategyVersion strategy_id={self.strategy_id} v{self.version}>"


class ModelConfig(Base, TimestampMixin):
    """Persistierte LLM-Konfiguration, damit Prompt-Versionen nachvollziehbar sind."""

    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN, nullable=True)
