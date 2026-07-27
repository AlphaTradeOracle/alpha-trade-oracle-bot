"""API-Schemas fuer Backtests."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import DISCLAIMER_TEXT


class BacktestRequest(BaseModel):
    """Anfrage fuer einen Backtest."""

    symbol: str = Field(min_length=3, max_length=32, examples=["BTCUSDT"])
    timeframe: str = Field(default="1h", examples=["1h"])
    start: datetime
    end: datetime
    fee_percent: float = Field(default=0.1, ge=0.0, le=5.0)
    slippage_percent: float = Field(default=0.05, ge=0.0, le=5.0)
    initial_capital: float = Field(default=10_000.0, gt=0.0)

    @model_validator(mode="after")
    def _validate_period(self) -> BacktestRequest:
        if self.end <= self.start:
            raise ValueError("Das Enddatum muss nach dem Startdatum liegen")
        return self


class BacktestTradeResponse(BaseModel):
    model_config = {"from_attributes": True}

    direction: str
    entry_at: datetime
    entry_price: float
    exit_at: datetime | None
    exit_price: float | None
    exit_reason: str | None
    net_pnl: float
    pnl_percent: float | None
    holding_minutes: int | None
    signal_score: float | None


class BacktestResponse(BaseModel):
    """Ergebnis eines Backtests."""

    run_id: int | None
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    status: str
    candles_loaded: int
    trade_count: int
    metrics: dict[str, dict[str, float]] = Field(
        description=(
            "Kennzahlen je Auswertungsbereich (overall, long, short, symbol:*, timeframe:*)."
        )
    )
    note: str = (
        "Historische Simulationsergebnisse sind keine Zusage fuer zukuenftige Ergebnisse. "
        "Es wurden keine echten Orders ausgefuehrt."
    )
    disclaimer: str = DISCLAIMER_TEXT
