export { createMockMarketData } from './mockProvider'
export type { MockAnchors, MockProviderOptions } from './mockProvider'
export {
  INTERVAL_SECONDS,
  type CandleInterval,
  type CandleRequest,
  type MarketDataProvider,
} from './types'

/**
 * Swap point for live data.
 *
 * Today the trade chart builds a mock provider seeded with the trade's own
 * prices. To go live, implement `MarketDataProvider` for Binance / Bybit /
 * Hyperliquid and return it from here — no component changes required.
 */
export const ACTIVE_PROVIDER_ID = 'mock' as const
