import type { MarketRegimeSnapshot } from '../../types/trade'

function biasTone(bias?: string | null): string {
  switch (bias) {
    case 'strong_bullish':
    case 'bullish':
      return 'text-emerald-400'
    case 'strong_bearish':
    case 'bearish':
      return 'text-rose-400'
    default:
      return 'text-amber-300'
  }
}

function biasGlyph(bias?: string | null): string {
  switch (bias) {
    case 'strong_bullish':
      return '● Strong Bullish'
    case 'bullish':
      return '● Bullish'
    case 'strong_bearish':
      return '● Strong Bearish'
    case 'bearish':
      return '● Bearish'
    default:
      return '● Neutral'
  }
}

function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)}%`
}

function fmtNum(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

interface MarketRegimeCardProps {
  regime: MarketRegimeSnapshot | null
}

/** Dashboard card: live global market regime summary. */
export function MarketRegimeCard({ regime }: MarketRegimeCardProps) {
  if (!regime) {
    return (
      <section className="panel p-4 sm:p-5">
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-secondary)]">
          Market Regime
        </h3>
        <p className="text-sm text-[var(--color-text-muted)]">Keine Marktdaten geladen.</p>
      </section>
    )
  }

  const rows: Array<{ label: string; value: string }> = [
    { label: 'BTC Trend', value: regime.btcTrend ?? '—' },
    { label: 'BTC Bias', value: regime.btcBias ?? '—' },
    { label: 'BTC.D', value: fmtPct(regime.btcD) },
    { label: 'USDT.D', value: fmtPct(regime.usdtD) },
    { label: 'Funding', value: regime.fundingStatus ?? '—' },
    {
      label: 'Fear & Greed',
      value:
        regime.fearGreed != null
          ? `${regime.fearGreed}${regime.fearGreedBand ? ` · ${regime.fearGreedBand}` : ''}`
          : '—',
    },
    {
      label: 'Liquidity Score',
      value:
        regime.liquidityScore != null ? fmtNum(regime.liquidityScore) : '—',
    },
    { label: 'Global Score', value: fmtNum(regime.globalScore) },
  ]

  return (
    <section className="panel p-4 sm:p-5">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-secondary)]">
          Market Regime
        </h3>
        {!regime.available ? (
          <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
            degraded
          </span>
        ) : null}
      </div>
      <p className={`text-lg font-semibold ${biasTone(regime.bias)}`}>
        {regime.biasLabel || biasGlyph(regime.bias)}
      </p>
      <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {rows.map((row) => (
          <div key={row.label} className="min-w-0">
            <dt className="text-[10px] font-medium uppercase tracking-[0.09em] text-[var(--color-text-muted)]">
              {row.label}
            </dt>
            <dd className="mt-1 truncate text-sm tabular text-[var(--color-text)]">{row.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
