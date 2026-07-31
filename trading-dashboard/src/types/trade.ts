/**
 * Core domain types for the trading desk.
 * Keep these transport-agnostic so JSON mock data and later REST/WebSocket
 * adapters can share the same contracts.
 */

export type TradeSide = 'LONG' | 'SHORT'

/** Lifecycle of a trade / order in the desk. */
export type TradeStatus = 'OPEN' | 'PENDING' | 'CLOSED'

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
