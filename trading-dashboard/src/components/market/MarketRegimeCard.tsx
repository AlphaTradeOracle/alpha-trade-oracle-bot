import type { MarketRegimeSnapshot } from '../../types/trade'

type Tone = 'neutral' | 'positive' | 'negative' | 'accent'

function biasTone(bias?: string | null): Tone {
  switch (bias) {
    case 'strong_bullish':
    case 'bullish':
      return 'positive'
    case 'strong_bearish':
    case 'bearish':
      return 'negative'
    default:
      return 'accent'
  }
}

function biasLabel(bias?: string | null, fallback?: string | null): string {
  if (fallback?.trim()) return fallback.trim()
  switch (bias) {
    case 'strong_bullish':
      return 'Strong Bullish'
    case 'bullish':
      return 'Bullish'
    case 'strong_bearish':
      return 'Strong Bearish'
    case 'bearish':
      return 'Bearish'
    default:
      return 'Neutral'
  }
}

function humanize(value?: string | null): string {
  if (!value) return '—'
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)}%`
}

function fmtNum(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

function scoreTone(value: number | null | undefined): Tone {
  if (value == null || Number.isNaN(value) || value === 0) return 'neutral'
  return value > 0 ? 'positive' : 'negative'
}

function fundingTone(status?: string | null): Tone {
  const s = (status ?? '').toLowerCase()
  if (s.includes('bull') || s.includes('positive') || s.includes('long')) return 'positive'
  if (s.includes('bear') || s.includes('negative') || s.includes('short')) return 'negative'
  return 'neutral'
}

function fearTone(value: number | null | undefined): Tone {
  if (value == null || Number.isNaN(value)) return 'neutral'
  if (value >= 55) return 'positive'
  if (value <= 40) return 'negative'
  return 'accent'
}

const valueToneClass: Record<Tone, string> = {
  neutral: 'text-[var(--color-text)]',
  positive: 'text-[var(--color-long)]',
  negative: 'text-[var(--color-short)]',
  accent: 'text-[var(--color-accent)]',
}

const biasBannerClass: Record<Tone, string> = {
  neutral: 'bg-[var(--color-surface-hover)] text-[var(--color-text)]',
  positive: 'bg-[var(--color-long-soft)] text-[var(--color-long)]',
  negative: 'bg-[var(--color-short-soft)] text-[var(--color-short)]',
  accent: 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]',
}

interface Metric {
  label: string
  value: string
  tone?: Tone
}

interface MarketRegimeCardProps {
  regime: MarketRegimeSnapshot | null
}

/** Dashboard card: live market sentiment / regime summary. */
export function MarketRegimeCard({ regime }: MarketRegimeCardProps) {
  if (!regime) {
    return (
      <section className="panel p-4 sm:p-5">
        <h3 className="mb-3 text-center text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-muted)]">
          Market Sentiment
        </h3>
        <p className="text-center text-sm text-[var(--color-text-muted)]">Keine Marktdaten geladen.</p>
      </section>
    )
  }

  const tone = biasTone(regime.bias)
  const metrics: Metric[] = [
    { label: 'BTC Trend', value: humanize(regime.btcTrend), tone: biasTone(regime.btcTrend) },
    { label: 'BTC Bias', value: humanize(regime.btcBias), tone: biasTone(regime.btcBias) },
    { label: 'BTC.D', value: fmtPct(regime.btcD) },
    { label: 'USDT.D', value: fmtPct(regime.usdtD) },
    {
      label: 'Funding',
      value: humanize(regime.fundingStatus),
      tone: fundingTone(regime.fundingStatus),
    },
    {
      label: 'Fear & Greed',
      value:
        regime.fearGreed != null
          ? `${regime.fearGreed}${regime.fearGreedBand ? ` · ${humanize(regime.fearGreedBand)}` : ''}`
          : '—',
      tone: fearTone(regime.fearGreed),
    },
    {
      label: 'Liquidity',
      value: regime.liquidityScore != null ? fmtNum(regime.liquidityScore) : '—',
      tone: scoreTone(regime.liquidityScore),
    },
    {
      label: 'Global Score',
      value: fmtNum(regime.globalScore),
      tone: scoreTone(regime.globalScore),
    },
  ]

  return (
    <section className="panel p-4 sm:p-5">
      <div className="relative mb-3 flex items-center justify-center gap-3">
        <h3 className="text-center text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-muted)]">
          Market Sentiment
        </h3>
        {!regime.available ? (
          <span className="absolute right-0 rounded-md bg-[var(--color-surface-hover)] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
            Degraded
          </span>
        ) : null}
      </div>

      <div
        className={`flex items-center justify-center rounded-lg px-4 py-3 text-center ${biasBannerClass[tone]}`}
      >
        <p className="text-base font-semibold tracking-tight sm:text-lg">
          {biasLabel(regime.bias, regime.biasLabel)}
        </p>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4 sm:gap-3">
        {metrics.map((m) => (
          <div
            key={m.label}
            className="flex min-w-0 flex-col items-center justify-center gap-1 rounded-lg bg-[var(--color-surface-hover)]/55 px-2 py-3 text-center"
          >
            <dt className="text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
              {m.label}
            </dt>
            <dd
              className={`w-full truncate text-sm font-semibold tabular tracking-tight ${valueToneClass[m.tone ?? 'neutral']}`}
            >
              {m.value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
