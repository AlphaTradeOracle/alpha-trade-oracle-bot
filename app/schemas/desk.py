"""CamelCase schemas for the public Alpha Desk dashboard API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DeskTakeProfit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    price: float
    size: float | None = None
    hit: bool = False


class DeskTrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    symbol: str
    side: str
    entry: float
    mark: float | None = None
    exit: float | None = None
    stop: float
    upnl: float | None = None
    realized: float | None = None
    r: float | None = None
    margin: float
    score: float
    status: str
    openedAt: str
    closedAt: str | None = None
    entryZoneLow: float | None = None
    entryZoneHigh: float | None = None
    strategy: str | None = None
    takeProfits: list[DeskTakeProfit] = Field(default_factory=list)
    positionSize: float | None = None
    leverage: float | None = None
    fees: float | None = None
    notes: str | None = None


class DeskPortfolio(BaseModel):
    model_config = ConfigDict(extra="forbid")

    totalCapital: float
    equity: float
    cash: float
    marginLocked: float
    realizedPnl: float
    openUpnl: float
    openR: float
    totalReturnPct: float
    openPositions: int
    pendingOrders: int
    closedTrades: int
    equityChangePct: float | None = None
    realizedChangePct: float | None = None


class DeskEquityPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: str
    equity: float


class DeskSnapshot(BaseModel):
    """One-shot payload for dashboard load/refresh."""

    model_config = ConfigDict(extra="forbid")

    portfolio: DeskPortfolio
    trades: list[DeskTrade]
    equity: list[DeskEquityPoint]
    generatedAt: str
