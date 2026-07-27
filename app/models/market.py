"""Modelle fuer Instrumente, Kerzen und Indikator-Snapshots."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import BIG_AMOUNT, JSON_COLUMN, PRICE, RATIO, SCORE, Base, TimestampMixin


class Asset(Base, TimestampMixin):
    """Ein handelbares Instrument, z. B. BTCUSDT auf Binance."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    base_asset: Mapped[str] = mapped_column(String(16), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(16), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="binance")
    price_precision: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    quantity_precision: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    candles: Mapped[list[MarketCandle]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Asset {self.symbol}>"


class MarketCandle(Base):
    """Normalisierte OHLCV-Kerze. Unfertige Kerzen werden nie fuer Signale genutzt."""

    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint("asset_id", "timeframe", "open_time", name="uq_candle_asset_tf_time"),
        Index("ix_candle_lookup", "asset_id", "timeframe", "open_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    high: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    low: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    close: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    volume: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    quote_volume: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    asset: Mapped[Asset] = relationship(back_populates="candles")


class IndicatorSnapshot(Base):
    """Indikatorwerte zum Zeitpunkt einer Analyse.

    Fachlich relevante Kennzahlen sind eigene Spalten, damit sie auswertbar
    bleiben. ``extra_values`` nimmt nur ergaenzende Werte auf.
    """

    __tablename__ = "indicator_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "asset_id", "timeframe", "candle_open_time", name="uq_snapshot_asset_tf_candle"
        ),
        Index("ix_snapshot_lookup", "asset_id", "timeframe", "candle_open_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    candle_open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)

    ema_9: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    ema_20: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    ema_50: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    ema_100: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    ema_200: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    sma_50: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    sma_200: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)

    rsi_14: Mapped[Decimal | None] = mapped_column(RATIO, nullable=True)
    macd: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    macd_signal: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    macd_histogram: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    stoch_rsi_k: Mapped[Decimal | None] = mapped_column(RATIO, nullable=True)
    stoch_rsi_d: Mapped[Decimal | None] = mapped_column(RATIO, nullable=True)
    roc_14: Mapped[Decimal | None] = mapped_column(RATIO, nullable=True)

    bb_upper: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    bb_middle: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    bb_lower: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    bb_width: Mapped[Decimal | None] = mapped_column(RATIO, nullable=True)
    atr_14: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    atr_percent: Mapped[Decimal | None] = mapped_column(RATIO, nullable=True)

    adx_14: Mapped[Decimal | None] = mapped_column(RATIO, nullable=True)
    plus_di: Mapped[Decimal | None] = mapped_column(RATIO, nullable=True)
    minus_di: Mapped[Decimal | None] = mapped_column(RATIO, nullable=True)

    obv: Mapped[Decimal | None] = mapped_column(BIG_AMOUNT, nullable=True)
    volume_ma_20: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    volume_ratio: Mapped[Decimal | None] = mapped_column(RATIO, nullable=True)

    supertrend: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    supertrend_direction: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vwap: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)

    trend_direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trend_strength: Mapped[Decimal | None] = mapped_column(SCORE, nullable=True)
    structure_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nearest_support: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    nearest_resistance: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)

    extra_values: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN, nullable=True)
