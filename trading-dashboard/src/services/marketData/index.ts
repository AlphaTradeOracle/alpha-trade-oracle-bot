export { createMockMarketData } from './mockProvider'
export type { MockAnchors, MockProviderOptions } from './mockProvider'
export { createPriceCurve } from './priceSeries'
export type { PriceAnchor, PriceCurve } from './priceSeries'
export { HistoricalDataProvider } from './HistoricalDataProvider'
export type { HistoricalDataProviderOptions } from './HistoricalDataProvider'
export {
  INTERVAL_SECONDS,
  TIMEFRAMES,
  TIMEFRAME_LABELS,
  alignToInterval,
  type CandleInterval,
  type CandleRequest,
  type MarketDataProvider,
} from './types'

/**
 * Swap point for live data.
 *
 * Chart components only talk to `HistoricalDataProvider`, which in turn talks
 * to a `MarketDataProvider`. Implementing that interface for Binance, Bybit or
 * Hyperliquid — REST paging first, WebSocket updates later — is enough to go
 * live without touching any component.
 */
export const ACTIVE_PROVIDER_ID = 'mock' as const
