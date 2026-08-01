import type { Candle } from '../../types/trade'

/** Supported kline intervals — mirrors common exchange notation. */
export type CandleInterval = '1m' | '5m' | '15m' | '1h' | '4h' | '1d'

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
 * The mock provider ships today; Binance / Bybit / Hyperliquid adapters can be
 * added later without touching chart or modal components.
 */
export interface MarketDataProvider {
  readonly id: string
  getCandles(request: CandleRequest): Promise<Candle[]>
}

export const INTERVAL_SECONDS: Record<CandleInterval, number> = {
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '1h': 3600,
  '4h': 14_400,
  '1d': 86_400,
}
