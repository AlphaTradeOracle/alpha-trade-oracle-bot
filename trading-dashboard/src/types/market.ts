/** Global market-regime types for the desk. */

export type MarketBias =
  | 'strong_bullish'
  | 'bullish'
  | 'neutral'
  | 'bearish'
  | 'strong_bearish'

export interface MarketRegimeSnapshot {
  asof?: string | null
  status: MarketBias | string
  marketScore?: number | null
  available?: boolean
  detail?: string | null
  btcTrend?: string | null
  btcBias?: string | null
  btcScore?: number | null
  btcDominance?: number | null
  usdtDominance?: number | null
  fundingStatus?: string | null
  fundingRate?: number | null
  fearGreed?: string | null
  fearGreedValue?: number | null
}

export interface TradeMarketContext {
  asof?: string | null
  overallBias?: string | null
  marketScore?: number | null
  btcPrice?: number | null
  btcBias?: string | null
  btcTrend?: string | null
  btcRsi?: number | null
  btcEmaStatus?: string | null
  btcVolatility?: number | null
  btcDominance?: number | null
  usdtDominance?: number | null
  fearGreed?: string | null
  fundingRate?: number | null
  openInterest?: number | null
  liquidations?: {
    long?: number | null
    short?: number | null
  } | null
}

export function biasLabel(bias: string | null | undefined): string {
  switch ((bias || '').toLowerCase()) {
    case 'strong_bullish':
      return 'Strong Bullish'
    case 'bullish':
      return 'Bullish'
    case 'bearish':
      return 'Bearish'
    case 'strong_bearish':
      return 'Strong Bearish'
    case 'neutral':
      return 'Neutral'
    default:
      return bias || '—'
  }
}

export function biasTone(
  bias: string | null | undefined,
): 'positive' | 'negative' | 'neutral' | 'warn' {
  const key = (bias || '').toLowerCase()
  if (key.includes('bullish')) return 'positive'
  if (key.includes('bearish')) return 'negative'
  if (key === 'neutral') return 'warn'
  return 'neutral'
}
