"""Schemas fuer Paper-Trading."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PaperSummaryResponse(BaseModel):
    cash_balance: float
    initial_balance: float
    realized_pnl: float
    open_positions: int
    open_margin: float
    equity: float
    win_rate: float
    closed_trades: int
    profit_factor: float


class PaperPositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    direction: str
    status: str
    timeframe: str
    entry_price: float
    stop_loss: float
    current_stop: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    initial_quantity: float
    remaining_quantity: float
    margin_used: float
    notional: float
    leverage: float
    tp1_filled: bool
    tp2_filled: bool
    tp3_filled: bool
    realized_pnl: float
    fees: float
    signal_score: float | None = None
    exit_reason: str | None = None
    opened_at: datetime
    closed_at: datetime | None = None


class PaperUpdateResponse(BaseModel):
    updated: int = Field(description="Anzahl der Positionen mit Statusaenderung")
    prices: int = Field(description="Anzahl geladener Kurse")
    open_positions: int
