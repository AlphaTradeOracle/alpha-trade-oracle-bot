"""CamelCase schemas for the public Alpha Desk dashboard API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DeskTakeProfit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    price: float
    size: float | None = None
    hit: bool = False


class DeskMarketRegime(BaseModel):
    """Live global market regime card payload."""

    model_config = ConfigDict(extra="forbid")

    bias: str
    biasLabel: str
    btcTrend: str | None = None
    btcBias: str | None = None
    btcD: float | None = None
    btcDTrend: str | None = None
    usdtD: float | None = None
    usdtRiskMode: str | None = None
    fundingStatus: str | None = None
    fearGreed: int | None = None
    fearGreedBand: str | None = None
    liquidityScore: float | None = None
    liquidityVenues: list[str] = Field(default_factory=list)
    globalScore: float | None = None
    available: bool = False
    capturedAt: str | None = None


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
    marketContext: dict | None = None


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


class DeskCandle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class DeskSnapshot(BaseModel):
    """One-shot payload for dashboard load/refresh."""

    model_config = ConfigDict(extra="forbid")

    portfolio: DeskPortfolio
    trades: list[DeskTrade]
    equity: list[DeskEquityPoint]
    generatedAt: str
    marketRegime: DeskMarketRegime | None = None
