/** Metrics that can be layered onto the equity chart. */
export type EquityOverlayId =
  | 'equity'
  | 'balance'
  | 'drawdown'
  | 'dailyReturn'
  | 'weeklyReturn'
  | 'monthlyReturn'
  | 'openEquity'
  | 'cashFlow'

export interface EquityOverlayDefinition {
  id: EquityOverlayId
  label: string
  color: string
  /** Overlays without a data source yet stay visible but inactive. */
  available: boolean
}

export const EQUITY_OVERLAYS: EquityOverlayDefinition[] = [
  { id: 'equity', label: 'Equity', color: '#4aa3ff', available: true },
  { id: 'balance', label: 'Balance', color: '#9aabbd', available: true },
  { id: 'drawdown', label: 'Drawdown', color: '#f07178', available: true },
  { id: 'dailyReturn', label: 'Daily Return', color: '#3dcf8e', available: false },
  { id: 'weeklyReturn', label: 'Weekly Return', color: '#3dcf8e', available: false },
  { id: 'monthlyReturn', label: 'Monthly Return', color: '#3dcf8e', available: false },
  { id: 'openEquity', label: 'Open Equity', color: '#e6b35c', available: false },
  { id: 'cashFlow', label: 'Deposits / Withdrawals', color: '#e6b35c', available: false },
]

export const DEFAULT_OVERLAYS: EquityOverlayId[] = ['equity']
