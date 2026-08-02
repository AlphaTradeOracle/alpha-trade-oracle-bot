/**
 * Core domain types for the trading desk.
 * Keep these transport-agnostic so JSON mock data and later REST/WebSocket
 * adapters can share the same contracts.
 */

export type TradeSide = 'LONG' | 'SHORT'

/** Lifecycle of a trade / order in the desk. */
export type TradeStatus = 'OPEN' | 'PENDING' | 'CLOSED'

/** A single take-profit ladder step. */
export interface TakeProfit {
  /** Display label, e.g. "TP1" */
  label: string
  price: number
  /** Share of the position closed at this level (0–1), when known */
  size?: number | null
  /** Whether the level was reached */
  hit?: boolean
}

/** OHLCV bar as delivered by exchange kline endpoints. */
export interface Candle {
  /** Unix seconds — matches Binance/Bybit/Hyperliquid kline open time */
  time: number
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

export interface Trade {
  id: string
  symbol: string
  side: TradeSide
  /** Average fill / intended entry price */
  entry: number
  /** Live mark (OPEN) or last known quote (PENDING) */
  mark: number | null
  /** Exit fill price when CLOSED */
  exit: number | null
  stop: number
  /** Unrealized PnL in quote currency (OPEN) */
  upnl: number | null
  /** Realized PnL in quote currency (CLOSED) */
  realized: number | null
  /** R-multiple vs initial risk */
  r: number | null
  /** Margin locked for the position */
  margin: number
  /** Strategy quality score 0–100 */
  score: number
  status: TradeStatus
  openedAt: string
  closedAt: string | null
  /** Optional pending entry zone bounds */
  entryZoneLow?: number | null
  entryZoneHigh?: number | null

  /* ---- Detail view fields (optional so existing mock rows stay valid) ---- */

  /** Strategy / setup name */
  strategy?: string
  /** Take-profit ladder, ordered TP1..TP4 */
  takeProfits?: TakeProfit[]
  /** Position size in base asset */
  positionSize?: number
  /** Applied leverage multiplier */
  leverage?: number
  /** Trading fees paid in quote currency */
  fees?: number
  /** Free-form journal text */
  notes?: string
  /** Market regime snapshot captured at arm/entry */
  marketContext?: MarketContext | null
}

export type MarketBias =
  | 'strong_bullish'
  | 'bullish'
  | 'neutral'
  | 'bearish'
  | 'strong_bearish'

export interface MarketContext {
  bias?: MarketBias | string
  biasLabel?: string
  globalScore?: number
  available?: boolean
  capturedAt?: string
  btc?: {
    price?: number | null
    bias?: string | null
    trend?: string | null
    rsi?: number | null
    emaStatus?: string | null
    volatility?: number | null
    atrPercent?: number | null
  }
  eth?: {
    available?: boolean
    bias?: string | null
    relativeStrengthVsBtc?: number | null
  }
  dominance?: {
    btcD?: number | null
    btcDTrend?: string | null
    usdtD?: number | null
    usdtRiskMode?: string | null
    total3Trend?: string | null
  }
  fearGreed?: {
    value?: number | null
    band?: string | null
  }
  funding?: {
    status?: string | null
    symbolRate?: number | null
    btcRate?: number | null
  }
  openInterest?: {
    available?: boolean
    relation?: string | null
    symbolOi?: number | null
    changePct?: number | null
  }
  liquidations?: {
    available?: boolean
    longUsd?: number | null
    shortUsd?: number | null
    liquidityScore?: number | null
    venues?: string[]
    longShare?: number | null
    bookImbalance?: number | null
    avgFunding?: number | null
    source?: string | null
  }
  blend?: {
    coinScore?: number
    finalScore?: number
    globalScore?: number
    detail?: string
  }
  detail?: string
}

export interface MarketRegimeSnapshot {
  bias: MarketBias | string
  biasLabel: string
  btcTrend?: string | null
  btcBias?: string | null
  btcD?: number | null
  btcDTrend?: string | null
  usdtD?: number | null
  usdtRiskMode?: string | null
  fundingStatus?: string | null
  fearGreed?: number | null
  fearGreedBand?: string | null
  liquidityScore?: number | null
  liquidityVenues?: string[]
  globalScore?: number | null
  available?: boolean
  capturedAt?: string | null
}

export interface EquityPoint {
  /** ISO date or datetime */
  t: string
  equity: number
}

export interface PortfolioSnapshot {
  /** Starting / reference capital */
  totalCapital: number
  /** Mark-to-market equity */
  equity: number
  /** Free cash */
  cash: number
  /** Margin currently locked in open positions */
  marginLocked: number
  /** Sum of closed realized PnL */
  realizedPnl: number
  /** Sum of open unrealized PnL */
  openUpnl: number
  /** Open R exposure (sum of open R) */
  openR: number
  /** Equity return vs totalCapital, percent */
  totalReturnPct: number
  openPositions: number
  pendingOrders: number
  closedTrades: number
  /** Closed-trade win rate in percent (0–100) */
  winRatePct?: number
  /** Optional day-over-day equity change (%) for KPI delta */
  equityChangePct?: number
  realizedChangePct?: number
}

export type PnLFilter = 'all' | 'profit' | 'loss'
export type SideFilter = 'all' | TradeSide
export type SortKey =
  | 'openedAt'
  | 'symbol'
  | 'score'
  | 'upnl'
  | 'realized'
  | 'r'
  | 'closedAt'

export interface TradeFilterState {
  query: string
  side: SideFilter
  minScore: number
  pnl: PnLFilter
  sortBy: SortKey
  sortDir: 'asc' | 'desc'
}
