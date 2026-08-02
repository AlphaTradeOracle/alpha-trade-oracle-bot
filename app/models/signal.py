"""Signale, ihr Score-Breakdown und das Zustellprotokoll."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import JSON_COLUMN, PRICE, RATIO, SCORE, WEIGHT, Base, TimestampMixin


class Signal(Base, TimestampMixin):
    """Das zentrale Analyseergebnis. Enthaelt niemals eine Order-Anweisung."""

    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signal_asset_created", "asset_id", "created_at"),
        Index("ix_signal_direction_created", "direction", "created_at"),
        Index("ix_signal_fingerprint", "fingerprint"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    analyzed_timeframes: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    market_phase: Mapped[str] = mapped_column(String(32), nullable=False)

    score: Mapped[float] = mapped_column(SCORE, nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)

    reference_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    entry_low: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    entry_high: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    stop_loss: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    take_profit_1: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    take_profit_2: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    take_profit_3: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)

    risk_reward_ratio: Mapped[float | None] = mapped_column(RATIO, nullable=True)
    risk_percent: Mapped[float | None] = mapped_column(RATIO, nullable=True)
    #: Rein informativ, es werden keine Orders erzeugt.
    suggested_position_size: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)

    data_quality: Mapped[float] = mapped_column(SCORE, nullable=False, default=100.0)
    invalidation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasons: Mapped[list[str] | None] = mapped_column(JSON_COLUMN, nullable=True)
    counter_arguments: Mapped[list[str] | None] = mapped_column(JSON_COLUMN, nullable=True)
    indicators_used: Mapped[list[str] | None] = mapped_column(JSON_COLUMN, nullable=True)
    llm_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Snapshot from MarketRegimeEngine (BTC bias, funding stubs, weights, …).
    market_context: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN, nullable=True)
    coin_score: Mapped[float | None] = mapped_column(SCORE, nullable=True)

    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    is_dispatched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    score_components: Mapped[list[SignalScoreComponent]] = relationship(
        back_populates="signal", cascade="all, delete-orphan", lazy="selectin"
    )
    deliveries: Mapped[list[SignalDelivery]] = relationship(
        back_populates="signal", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Signal id={self.id} asset_id={self.asset_id} {self.direction} {self.score}>"


class SignalScoreComponent(Base):
    """Eine Score-Kategorie eines Signals — relational, nicht als JSON-Blob."""

    __tablename__ = "signal_score_components"
    __table_args__ = (
        UniqueConstraint("signal_id", "category", name="uq_score_component_signal_category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Rohwert im Bereich -100..+100, Vorzeichen gibt die Richtung an.
    raw_score: Mapped[float] = mapped_column(SCORE, nullable=False)
    weight: Mapped[float] = mapped_column(WEIGHT, nullable=False)
    weighted_score: Mapped[float] = mapped_column(RATIO, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    signal: Mapped[Signal] = relationship(back_populates="score_components")


class SignalDelivery(Base):
    """Protokoll je Signal und Chat — auch unterdrueckte Signale werden erfasst."""

    __tablename__ = "signal_deliveries"
    __table_args__ = (
        UniqueConstraint("signal_id", "telegram_chat_id", name="uq_delivery_signal_chat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), nullable=False
    )
    telegram_chat_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_chats.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    suppression_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    signal: Mapped[Signal] = relationship(back_populates="deliveries")


class LLMRequest(Base):
    """Protokoll eines LLM-Aufrufs inkl. Tokenverbrauch und Validierungsstatus."""

    __tablename__ = "llm_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(
        ForeignKey("signals.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()", index=True
    )
