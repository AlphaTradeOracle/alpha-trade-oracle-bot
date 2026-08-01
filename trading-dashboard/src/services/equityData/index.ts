export { createMockEquityData } from './mockEquityProvider'
export type { MockEquityOptions } from './mockEquityProvider'
export { HistoricalEquityProvider } from './HistoricalEquityProvider'
export type { HistoricalEquityProviderOptions } from './HistoricalEquityProvider'
export type { EquityDataProvider, EquitySample, EquitySeriesRequest } from './types'

/**
 * Swap point for live equity.
 *
 * The chart talks to `HistoricalEquityProvider`, which talks to an
 * `EquityDataProvider`. Implementing that interface against the account
 * endpoint — REST paging first, WebSocket updates later — is enough to go live.
 */
export const ACTIVE_EQUITY_PROVIDER_ID = 'mock' as const
