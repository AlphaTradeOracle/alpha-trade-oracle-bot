import { Globe2 } from 'lucide-react'
import type { MarketRegimeSnapshot } from '../../types/market'
import { biasLabel, biasTone } from '../../types/market'

interface MarketRegimeCardProps {
  regime: MarketRegimeSnapshot | null
}

const toneClass = {
  positive: 'text-[var(--color-long)]',
  negative: 'text-[var(--color-short)]',
  warn: 'text-[var(--color-warn)]',
  neutral: 'text-[var(--color-text-secondary)]',
} as const

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
        {label}
      </p>
      <p className="mt-0.5 truncate text-sm tabular text-[var(--color-text)]">{value}</p>
    </div>
  )
}

export function MarketRegimeCard({ regime }: MarketRegimeCardProps) {
  const status = regime?.status ?? 'neutral'
  const tone = biasTone(status)
  const score =
    regime?.marketScore != null ? `${regime.marketScore > 0 ? '+' : ''}${regime.marketScore.toFixed(1)}` : '—'

  return (
    <section className="panel p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
            Market Regime
          </p>
          <p className={`mt-1 text-2xl font-semibold tracking-tight ${toneClass[tone]}`}>
            {biasLabel(status)}
          </p>
          <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
            Gesamtbewertung · Score {score}
          </p>
        </div>
        <span className="rounded-lg bg-[var(--color-accent-soft)] p-2 text-[var(--color-accent)]">
          <Globe2 size={18} strokeWidth={1.8} />
        </span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Field label="BTC Trend" value={regime?.btcTrend ?? '—'} />
        <Field label="BTC Bias" value={biasLabel(regime?.btcBias)} />
        <Field
          label="BTC.D"
          value={regime?.btcDominance != null ? `${regime.btcDominance.toFixed(1)}%` : 'pending'}
        />
        <Field
          label="USDT.D"
          value={regime?.usdtDominance != null ? `${regime.usdtDominance.toFixed(1)}%` : 'pending'}
        />
        <Field label="Funding" value={regime?.fundingStatus ?? 'pending_feed'} />
        <Field label="Fear & Greed" value={regime?.fearGreed ?? 'pending'} />
      </div>
    </section>
  )
}
