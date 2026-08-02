import type { Trade } from '../../../types/trade'
import { biasLabel } from '../../../types/market'
import { DetailCard, DetailField } from './DetailField'

interface TradeMarketContextProps {
  trade: Trade
}

function fmt(value: number | null | undefined, digits = 2, suffix = ''): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)}${suffix}`
}

/**
 * Market conditions at trade entry — for post-trade review.
 */
export function TradeMarketContext({ trade }: TradeMarketContextProps) {
  const ctx = trade.marketContext
  if (!ctx) {
    return (
      <DetailCard title="Market Context">
        <p className="text-xs text-[var(--color-text-muted)]">
          Kein Markt-Snapshot für diesen Trade gespeichert.
        </p>
      </DetailCard>
    )
  }

  return (
    <DetailCard
      title="Market Context"
      actions={
        trade.coinScore != null ? (
          <span className="text-[11px] text-[var(--color-text-muted)]">
            Coin {trade.coinScore.toFixed(1)} → Final {trade.score.toFixed(1)}
          </span>
        ) : undefined
      }
    >
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <DetailField label="Gesamtbias">{biasLabel(ctx.overallBias)}</DetailField>
        <DetailField label="BTC Preis">{fmt(ctx.btcPrice, 0)}</DetailField>
        <DetailField label="BTC Bias">{biasLabel(ctx.btcBias)}</DetailField>
        <DetailField label="BTC Trend">{ctx.btcTrend ?? '—'}</DetailField>
        <DetailField label="BTC RSI">{fmt(ctx.btcRsi, 1)}</DetailField>
        <DetailField label="BTC EMA">{ctx.btcEmaStatus ?? '—'}</DetailField>
        <DetailField label="BTC Volatilität">{fmt(ctx.btcVolatility, 2, '%')}</DetailField>
        <DetailField label="BTC.D">
          {ctx.btcDominance != null ? fmt(ctx.btcDominance, 1, '%') : 'pending'}
        </DetailField>
        <DetailField label="USDT.D">
          {ctx.usdtDominance != null ? fmt(ctx.usdtDominance, 1, '%') : 'pending'}
        </DetailField>
        <DetailField label="Fear & Greed">{ctx.fearGreed ?? 'pending'}</DetailField>
        <DetailField label="Funding">
          {ctx.fundingRate != null ? fmt(ctx.fundingRate, 4, '%') : 'pending'}
        </DetailField>
        <DetailField label="Open Interest">
          {ctx.openInterest != null ? fmt(ctx.openInterest, 0) : 'later'}
        </DetailField>
      </dl>
    </DetailCard>
  )
}
