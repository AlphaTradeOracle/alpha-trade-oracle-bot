import type { Candle } from '../../types/trade'

/** Supported kline intervals — mirrors common exchange notation. */
export type CandleInterval =
  | '1m'
  | '5m'
  | '15m'
  | '30m'
  | '1h'
  | '4h'
  | '12h'
  | '1d'
  | '3d'
  | '1w'

export const INTERVAL_SECONDS: Record<CandleInterval, number> = {
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '30m': 1_800,
  '1h': 3_600,
  '4h': 14_400,
  '12h': 43_200,
  '1d': 86_400,
  '3d': 259_200,
  '1w': 604_800,
}

/** Timeframes offered in the chart toolbar, in display order. */
export const TIMEFRAMES: CandleInterval[] = [
  '1m',
  '5m',
  '30m',
  '1h',
  '4h',
  '12h',
  '1d',
  '3d',
  '1w',
]

export const TIMEFRAME_LABELS: Record<CandleInterval, string> = {
  '1m': '1m',
  '5m': '5m',
  '15m': '15m',
  '30m': '30m',
  '1h': '1H',
  '4h': '4H',
  '12h': '12H',
  '1d': '1D',
  '3d': '3D',
  '1w': '1W',
}

export interface CandleRequest {
  symbol: string
  interval: CandleInterval
  /** Inclusive window start (unix seconds) */
  from: number
  /** Inclusive window end (unix seconds) */
  to: number
}

/**
 * Contract every candle source must satisfy.
 *
 * The mock provider ships today; REST and WebSocket adapters for
 * Binance / Bybit / Hyperliquid can implement the same interface without
 * touching the chart components.
 */
export interface MarketDataProvider {
  readonly id: string
  getCandles(request: CandleRequest): Promise<Candle[]>
  /** Earliest timestamp the source can serve, when known. */
  readonly earliestTime?: number
}

/** Aligns a timestamp to the start of its candle. */
export function alignToInterval(time: number, interval: CandleInterval): number {
  const step = INTERVAL_SECONDS[interval]
  return Math.floor(time / step) * step
}
