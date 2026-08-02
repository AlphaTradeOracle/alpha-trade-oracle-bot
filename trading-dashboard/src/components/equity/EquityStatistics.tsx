import type { EquitySample } from '../../services/equityData'
import type { Trade } from '../../types/trade'
import { computeEquityStats } from '../../utils/equityStats'
import { formatMoney, formatPct, formatSignedMoney } from '../../utils/format'

interface EquityStatisticsProps {
  samples: EquitySample[]
  closedTrades: Trade[]
}

type Tone = 'positive' | 'negative' | 'neutral'

const toneClass: Record<Tone, string> = {
  positive: 'text-[var(--color-long)]',
  negative: 'text-[var(--color-short)]',
  neutral: 'text-[var(--color-text)]',
}

function toneOf(value: number | null | undefined, neutralAt = 0): Tone {
  if (value == null || value === neutralAt) return 'neutral'
  return value > neutralAt ? 'positive' : 'negative'
}

/** Headline metrics derived from the loaded equity window and closed book. */
export function EquityStatistics({ samples, closedTrades }: EquityStatisticsProps) {
  const stats = computeEquityStats(samples, closedTrades)

  const groups: { title: string; items: { label: string; value: string; tone: Tone }[] }[] = [
    {
      title: 'Kapital',
      items: [
        { label: 'Aktuelle Equity', value: formatMoney(stats.current), tone: 'neutral' },
        { label: 'Höchste Equity', value: formatMoney(stats.high), tone: 'positive' },
        { label: 'Niedrigste Equity', value: formatMoney(stats.low), tone: 'negative' },
      ],
    },
    {
      title: 'Rendite',
      items: [
        {
          label: 'Total Return',
          value: formatPct(stats.totalReturnPct),
          tone: toneOf(stats.totalReturnPct),
        },
        {
          label: 'CAGR',
          value: stats.cagrPct != null ? formatPct(stats.cagrPct) : '—',
          tone: toneOf(stats.cagrPct),
        },
        {
          label: 'Max Drawdown',
          value: formatPct(stats.maxDrawdownPct),
          tone: stats.maxDrawdownPct < 0 ? 'negative' : 'neutral',
        },
      ],
    },
    {
      title: 'Risiko',
      items: [
        { label: 'Sharpe Ratio', value: '—', tone: 'neutral' },
        { label: 'Sortino Ratio', value: '—', tone: 'neutral' },
        {
          label: 'Profit Factor',
          value: stats.profitFactor != null ? stats.profitFactor.toFixed(2) : '—',
          tone: toneOf(stats.profitFactor, 1),
        },
      ],
    },
    {
      title: 'Handel',
      items: [
        {
          label: 'Winrate',
          value: stats.winratePct != null ? `${stats.winratePct.toFixed(1)}%` : '—',
          tone: toneOf(stats.winratePct, 50),
        },
        {
          label: 'Avg Profit %',
          value:
            stats.averageR != null ? formatPct(stats.averageR * 100) : '—',
          tone: toneOf(stats.averageR),
        },
        {
          label: 'Ø Tagesgewinn',
          value:
            stats.averageDailyPnl != null ? formatSignedMoney(stats.averageDailyPnl) : '—',
          tone: toneOf(stats.averageDailyPnl),
        },
        {
          label: 'Ø Monatsgewinn',
          value:
            stats.averageMonthlyPnl != null ? formatSignedMoney(stats.averageMonthlyPnl) : '—',
          tone: toneOf(stats.averageMonthlyPnl),
        },
      ],
    },
  ]

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {groups.map((group) => (
        <section key={group.title} className="panel p-4">
          <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-secondary)]">
            {group.title}
          </h3>
          <dl className="space-y-2.5">
            {group.items.map((item) => (
              <div key={item.label} className="flex items-baseline justify-between gap-3">
                <dt className="text-xs text-[var(--color-text-muted)]">{item.label}</dt>
                <dd className={`tabular text-sm font-medium ${toneClass[item.tone]}`}>
                  {item.value}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  )
}
