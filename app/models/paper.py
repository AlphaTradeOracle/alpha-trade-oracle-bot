"""Paper-Trading: virtuelles Depot mit Scale-out an TP1/TP2/TP3."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import PRICE, Base, CreatedAtMixin, TimestampMixin


class PaperAccount(Base, TimestampMixin):
    """Ein virtuelles Depot fuer Paper-Trading."""

    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, default="default")
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="USDT")
    initial_balance: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    cash_balance: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(PRICE, nullable=False, default=Decimal("0"))
    margin_per_trade: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    leverage: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False, default=5.0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    positions: Mapped[list[PaperPosition]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class PaperPosition(Base, TimestampMixin):
    """Offene oder geschlossene Paper-Position."""

    __tablename__ = "paper_positions"
    __table_args__ = (
        Index("ix_paper_positions_status", "status"),
        Index("ix_paper_positions_symbol_status", "symbol", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False
    )
    signal_id: Mapped[int | None] = mapped_column(
        ForeignKey("signals.id", ondelete="SET NULL"), nullable=True
    )
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, default="1h")

    entry_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    current_stop: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    take_profit_1: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    take_profit_2: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    take_profit_3: Mapped[Decimal] = mapped_column(PRICE, nullable=False)

    initial_quantity: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    remaining_quantity: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    margin_used: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    notional: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    leverage: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)

    tp1_filled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tp2_filled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tp3_filled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    realized_pnl: Mapped[Decimal] = mapped_column(PRICE, nullable=False, default=Decimal("0"))
    fees: Mapped[Decimal] = mapped_column(PRICE, nullable=False, default=Decimal("0"))
    #: Dollar-Risiko der Position beim Entry (1R). Basis jeder R-Auswertung.
    risk_amount: Mapped[Decimal] = mapped_column(PRICE, nullable=False, default=Decimal("0"))
    signal_score: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    account: Mapped[PaperAccount] = relationship(back_populates="positions")
    fills: Mapped[list[PaperFill]] = relationship(
        back_populates="position", cascade="all, delete-orphan", lazy="selectin"
    )


class PaperFill(Base, CreatedAtMixin):
    """Teil-Fill einer Paper-Position (Entry oder Scale-out)."""

    __tablename__ = "paper_fills"
    __table_args__ = (Index("ix_paper_fills_position", "position_id", "filled_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("paper_positions.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    fee: Mapped[Decimal] = mapped_column(PRICE, nullable=False, default=Decimal("0"))
    pnl: Mapped[Decimal] = mapped_column(PRICE, nullable=False, default=Decimal("0"))
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    position: Mapped[PaperPosition] = relationship(back_populates="fills")
