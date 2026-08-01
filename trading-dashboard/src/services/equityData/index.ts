export { createMockEquityData } from './mockEquityProvider'
export type { MockEquityOptions } from './mockEquityProvider'
export { createDeskEquityData } from './deskEquityProvider'
export { HistoricalEquityProvider } from './HistoricalEquityProvider'
export type { HistoricalEquityProviderOptions } from './HistoricalEquityProvider'
export type { EquityDataProvider, EquitySample, EquitySeriesRequest } from './types'

/** Live desk equity from paper fills via snapshot `equity[]`. */
export const ACTIVE_EQUITY_PROVIDER_ID = 'desk' as const
