"""Backtest-Laeufe, simulierte Trades und Kennzahlen."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import JSON_COLUMN, PRICE, RATIO, SCORE, Base, TimestampMixin


class BacktestRun(Base, TimestampMixin):
    """Ein reproduzierbarer Backtest-Durchlauf."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fee_percent: Mapped[float] = mapped_column(RATIO, nullable=False, default=0.1)
    slippage_percent: Mapped[float] = mapped_column(RATIO, nullable=False, default=0.05)
    initial_capital: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN, nullable=True)

    trades: Mapped[list[BacktestTrade]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    metrics: Mapped[list[BacktestMetric]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )


class BacktestTrade(Base):
    """Ein simulierter Trade. Es wurde nie eine echte Order platziert."""

    __tablename__ = "backtest_trades"
    __table_args__ = (Index("ix_backtest_trade_run", "backtest_run_id", "entry_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backtest_run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)

    entry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    stop_loss: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    take_profit_1: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    take_profit_2: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    take_profit_3: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)

    quantity: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    gross_pnl: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    fees: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    net_pnl: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    pnl_percent: Mapped[float | None] = mapped_column(RATIO, nullable=True)
    risk_reward_planned: Mapped[float | None] = mapped_column(RATIO, nullable=True)
    holding_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signal_score: Mapped[float | None] = mapped_column(SCORE, nullable=True)

    run: Mapped[BacktestRun] = relationship(back_populates="trades")


class BacktestMetric(Base):
    """Eine Kennzahl je Auswertungsbereich.

    Zeilenbasiert statt breiter Tabelle: neue Kennzahlen brauchen keine Migration.
    ``scope`` ist z. B. ``overall``, ``long``, ``short``, ``symbol:BTCUSDT``
    oder ``timeframe:1h``.
    """

    __tablename__ = "backtest_metrics"
    __table_args__ = (
        UniqueConstraint(
            "backtest_run_id", "scope", "metric_name", name="uq_backtest_metric_scope_name"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backtest_run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default="overall")
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)

    run: Mapped[BacktestRun] = relationship(back_populates="metrics")
