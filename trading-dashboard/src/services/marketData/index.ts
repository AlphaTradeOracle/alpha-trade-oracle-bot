export { createMockMarketData } from './mockProvider'
export type { MockAnchors, MockProviderOptions } from './mockProvider'
export { createDeskMarketData } from './deskProvider'
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

/** Live desk candles via `/api/v1/desk/candles`. */
export const ACTIVE_PROVIDER_ID = 'desk' as const
