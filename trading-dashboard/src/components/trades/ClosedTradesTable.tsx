import type { Trade } from '../../types/trade'
import { formatDateTime, formatDuration, formatPrice } from '../../utils/format'
import { EmptyState } from '../ui/EmptyState'
import { PnLCell } from './PnLCell'
import { ScoreBadge } from './ScoreBadge'
import { SideBadge } from './SideBadge'

interface ClosedTradesTableProps {
  trades: Trade[]
}

export function ClosedTradesTable({ trades }: ClosedTradesTableProps) {
  if (trades.length === 0) {
    return (
      <div className="panel">
        <EmptyState title="No closed trades" description="Closed fills will appear here." />
      </div>
    )
  }

  return (
    <div className="panel overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border-subtle)] text-[11px] uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
              {['Symbol', 'Side', 'Entry', 'Exit', 'PnL', 'R', 'Score', 'Duration', 'Closed At'].map(
                (h) => (
                  <th key={h} className="px-4 py-3 font-medium whitespace-nowrap">
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr
                key={t.id}
                className="border-b border-[var(--color-border-subtle)]/80 transition-colors last:border-0 hover:bg-[var(--color-surface-hover)]/70"
              >
                <td className="px-4 py-3 font-medium whitespace-nowrap">{t.symbol}</td>
                <td className="px-4 py-3">
                  <SideBadge side={t.side} />
                </td>
                <td className="px-4 py-3 tabular text-[var(--color-text-secondary)]">
                  {formatPrice(t.entry)}
                </td>
                <td className="px-4 py-3 tabular">{formatPrice(t.exit)}</td>
                <td className="px-4 py-3">
                  <PnLCell value={t.realized} />
                </td>
                <td className="px-4 py-3">
                  <PnLCell value={t.r} asR />
                </td>
                <td className="px-4 py-3">
                  <ScoreBadge score={t.score} />
                </td>
                <td className="px-4 py-3 tabular text-[var(--color-text-muted)]">
                  {formatDuration(t.openedAt, t.closedAt)}
                </td>
                <td className="px-4 py-3 tabular text-[var(--color-text-muted)] whitespace-nowrap">
                  {formatDateTime(t.closedAt)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
