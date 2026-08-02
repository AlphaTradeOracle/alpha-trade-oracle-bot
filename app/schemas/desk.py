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
    #: Same hard-veto flag paper/scan use (source of truth = MarketRegimeEngine).
    hardVeto: bool | None = None
    #: Soft score blend active alongside hard veto.
    scoreBlend: bool | None = None


class DeskTrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    symbol: str
    side: str
    entry: float
    mark: float | None = None
    exit: float | None = None
    #: Original strategic stop (Risk/Unit). Prefer over currentStop after BE.
    stop: float
    #: Live managed stop (may be fee-aware BE after TP1).
    currentStop: float | None = None
    upnl: float | None = None
    #: Realized PnL including open scale-out partials.
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
    #: Base-asset quantity still open (OPEN) or initial (CLOSED).
    positionSize: float | None = None
    #: Quote notional for displayed size (remaining × entry when OPEN).
    notional: float | None = None
    #: Entry notional before scale-out.
    initialNotional: float | None = None
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
    #: Closed-trade realized PnL (matches Closed Trades KPI).
    realizedPnl: float
    #: Scale-out profits still sitting on OPEN rows.
    openRealizedPnl: float = 0.0
    #: Account ledger realized (closed + open partials).
    accountRealizedPnl: float | None = None
    openUpnl: float
    openR: float
    totalReturnPct: float
    openPositions: int
    pendingOrders: int
    closedTrades: int
    #: Closed-trade win rate in percent (0–100).
    winRatePct: float = 0.0
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


class DeskTopCoin(BaseModel):
    """Live top-market-cap coin tile for the desk banner."""

    model_config = ConfigDict(extra="forbid")

    id: str
    symbol: str
    name: str
    rank: int
    priceUsd: float
    change24hPct: float | None = None
    marketCapUsd: float | None = None
    volume24hUsd: float | None = None
    circulatingSupply: float | None = None
    imageUrl: str | None = None
    sparkline: list[float] = Field(default_factory=list)


class DeskTopCoinsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coins: list[DeskTopCoin]
    generatedAt: str
    source: str = "coingecko"
