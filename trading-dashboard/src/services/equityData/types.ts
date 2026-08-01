import type { CandleInterval } from '../marketData'

/** One sampled point of the account equity curve. */
export interface EquitySample {
  /** Unix seconds */
  time: number
  /** Mark-to-market equity */
  equity: number
  /** Equity excluding open positions */
  balance: number
  /** Peak-to-current drawdown in percent (≤ 0) */
  drawdownPct: number
}

export interface EquitySeriesRequest {
  interval: CandleInterval
  /** Inclusive window start (unix seconds) */
  from: number
  /** Inclusive window end (unix seconds) */
  to: number
}

/**
 * Contract every equity source must satisfy.
 *
 * The mock provider ships today; a REST endpoint or a WebSocket feed can
 * implement the same interface without touching the chart components.
 */
export interface EquityDataProvider {
  readonly id: string
  getSeries(request: EquitySeriesRequest): Promise<EquitySample[]>
  /** Earliest timestamp the source can serve, when known. */
  readonly earliestTime?: number
}
