import type { Trade } from '../../../types/trade'
import { formatPct, formatR, formatSignedMoney } from '../../../utils/format'
import { DetailCard } from './DetailField'

interface TradePerformanceProps {
  trade: Trade
}

type Tone = 'positive' | 'negative' | 'neutral'

function toneOf(value: number | null | undefined): Tone {
  if (value == null || value === 0) return 'neutral'
  return value > 0 ? 'positive' : 'negative'
}

const toneClass: Record<Tone, string> = {
  positive: 'text-[var(--color-long)]',
  negative: 'text-[var(--color-short)]',
  neutral: 'text-[var(--color-text)]',
}

/**
 * Headline outcome metrics.
 * Values come straight from the trade record — no derived analytics yet.
 */
export function TradePerformance({ trade }: TradePerformanceProps) {
  const pnl = trade.realized ?? trade.upnl ?? 0
  const returnPct = trade.margin > 0 ? (pnl / trade.margin) * 100 : 0
  const outcome =
    trade.status !== 'CLOSED' ? 'Offen' : pnl >= 0 ? 'Win' : 'Loss'

  const metrics = [
    { label: 'Ergebnis', value: outcome, tone: toneOf(trade.status === 'CLOSED' ? pnl : null) },
    { label: 'PnL', value: formatSignedMoney(pnl), tone: toneOf(pnl) },
    { label: 'Return', value: formatPct(returnPct), tone: toneOf(returnPct) },
    { label: 'R-Multiple', value: formatR(trade.r), tone: toneOf(trade.r) },
    { label: 'Score', value: trade.score.toFixed(1), tone: 'neutral' as Tone },
  ]

  // Score bar doubles as a quick visual quality read.
  const scorePct = Math.max(0, Math.min(100, trade.score))

  return (
    <DetailCard title="Performance">
      <div className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-3">
        {metrics.map((m) => (
          <div key={m.label} className="min-w-0">
            <p className="text-[10px] font-medium uppercase tracking-[0.09em] text-[var(--color-text-muted)]">
              {m.label}
            </p>
            <p className={`mt-1 truncate tabular text-[15px] font-semibold ${toneClass[m.tone]}`}>
              {m.value}
            </p>
          </div>
        ))}
      </div>

      <div className="mt-5 border-t border-[var(--color-border-subtle)] pt-4">
        <div className="mb-2 flex items-baseline justify-between text-[10px] uppercase tracking-[0.09em] text-[var(--color-text-muted)]">
          <span>Signal Score</span>
          <span className="tabular">{trade.score.toFixed(1)} / 100</span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-surface-hover)]">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${scorePct}%`,
              background:
                scorePct >= 70
                  ? 'var(--color-long)'
                  : scorePct >= 40
                    ? 'var(--color-warn)'
                    : 'var(--color-short)',
            }}
          />
        </div>
      </div>
    </DetailCard>
  )
}
