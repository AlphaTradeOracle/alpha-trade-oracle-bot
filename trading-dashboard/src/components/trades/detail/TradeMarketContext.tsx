import type { Trade } from '../../../types/trade'
import { DetailCard, DetailField } from './DetailField'

function fmt(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return Number(value).toFixed(digits)
}

function fmtRate(value: number | null | undefined): string {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `${(Number(value) * 100).toFixed(4)}%`
}

interface TradeMarketContextProps {
  trade: Trade
}

/** Entry-time market context for trade detail. */
export function TradeMarketContext({ trade }: TradeMarketContextProps) {
  const ctx = trade.marketContext
  if (!ctx) {
    return (
      <DetailCard title="Market Context">
        <p className="text-sm text-[var(--color-text-muted)]">
          Kein Market-Context für diesen Trade gespeichert.
        </p>
      </DetailCard>
    )
  }

  const btc = ctx.btc ?? {}
  const dom = ctx.dominance ?? {}
  const fg = ctx.fearGreed ?? {}
  const fund = ctx.funding ?? {}
  const oi = ctx.openInterest ?? {}
  const liq = ctx.liquidations ?? {}

  return (
    <DetailCard title="Market Context">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
        <DetailField label="Bias" strong>
          {ctx.biasLabel ?? ctx.bias ?? '—'}
        </DetailField>
        <DetailField label="BTC Preis">{fmt(btc.price, 2)}</DetailField>
        <DetailField label="BTC Bias">{btc.bias ?? '—'}</DetailField>
        <DetailField label="BTC Trend">{btc.trend ?? '—'}</DetailField>
        <DetailField label="BTC RSI">{fmt(btc.rsi, 1)}</DetailField>
        <DetailField label="BTC EMA">{btc.emaStatus ?? '—'}</DetailField>
        <DetailField label="BTC Volatilität">{fmt(btc.volatility ?? btc.atrPercent, 2)}</DetailField>
        <DetailField label="BTC.D">{dom.btcD != null ? `${fmt(dom.btcD)}%` : '—'}</DetailField>
        <DetailField label="USDT.D">{dom.usdtD != null ? `${fmt(dom.usdtD)}%` : '—'}</DetailField>
        <DetailField label="Fear & Greed">
          {fg.value != null ? `${fg.value}${fg.band ? ` · ${fg.band}` : ''}` : '—'}
        </DetailField>
        <DetailField label="Funding">{fund.status ?? '—'}</DetailField>
        <DetailField label="Funding Rate">{fmtRate(fund.symbolRate ?? fund.btcRate)}</DetailField>
        <DetailField label="Open Interest">
          {oi.available ? oi.relation ?? fmt(oi.symbolOi, 0) : '—'}
        </DetailField>
        <DetailField label="Liquidity Score">
          {liq.liquidityScore != null
            ? `${fmt(liq.liquidityScore, 1)}${
                liq.venues?.length ? ` · ${liq.venues.join('+')}` : ''
              }`
            : liq.available
              ? fmt(liq.longUsd, 0)
              : '—'}
        </DetailField>
        <DetailField label="Long Share">
          {liq.longShare != null ? `${fmt(liq.longShare * 100, 1)}%` : '—'}
        </DetailField>
        <DetailField label="Book Imbalance">
          {liq.bookImbalance != null ? fmt(liq.bookImbalance, 3) : '—'}
        </DetailField>
      </dl>
    </DetailCard>
  )
}
