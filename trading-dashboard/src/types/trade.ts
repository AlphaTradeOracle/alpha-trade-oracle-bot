/**
 * Core domain types for the trading desk.
 * Keep these transport-agnostic so JSON mock data and later REST/WebSocket
 * adapters can share the same contracts.
 */

import type { TradeMarketContext } from './market'

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
  /** Market snapshot at entry (from MarketRegimeEngine) */
  marketContext?: TradeMarketContext | null
  /** Coin-only score before market blend */
  coinScore?: number | null
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
