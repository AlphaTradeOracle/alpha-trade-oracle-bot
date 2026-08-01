import type { Trade } from '../../types/trade'
import { formatPrice, formatSince } from '../../utils/format'
import { EmptyState } from '../ui/EmptyState'
import { ScoreBadge } from './ScoreBadge'
import { SideBadge } from './SideBadge'

interface PendingTradesTableProps {
  trades: Trade[]
  onRowClick?: (trade: Trade) => void
}

function entryZone(t: Trade): string {
  if (t.entryZoneLow != null && t.entryZoneHigh != null) {
    return `${formatPrice(t.entryZoneLow)} – ${formatPrice(t.entryZoneHigh)}`
  }
  return formatPrice(t.entry)
}

export function PendingTradesTable({ trades, onRowClick }: PendingTradesTableProps) {
  if (trades.length === 0) {
    return (
      <div className="panel">
        <EmptyState title="No pending orders" description="Retest / limit setups show up here." />
      </div>
    )
  }

  return (
    <div className="panel overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border-subtle)] text-[11px] uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
              {['Symbol', 'Side', 'Entry Zone', 'Stop', 'Score', 'Since'].map((h) => (
                <th key={h} className="px-4 py-3 font-medium whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr
                key={t.id}
                onClick={() => onRowClick?.(t)}
                className={[
                  'border-b border-[var(--color-border-subtle)]/80 transition-colors last:border-0 hover:bg-[var(--color-surface-hover)]/70',
                  onRowClick ? 'cursor-pointer' : '',
                ].join(' ')}
              >
                <td className="px-4 py-3 font-medium whitespace-nowrap">{t.symbol}</td>
                <td className="px-4 py-3">
                  <SideBadge side={t.side} />
                </td>
                <td className="px-4 py-3 tabular text-[var(--color-text-secondary)] whitespace-nowrap">
                  {entryZone(t)}
                </td>
                <td className="px-4 py-3 tabular">{formatPrice(t.stop)}</td>
                <td className="px-4 py-3">
                  <ScoreBadge score={t.score} />
                </td>
                <td className="px-4 py-3 tabular text-[var(--color-text-muted)]">
                  {formatSince(t.openedAt)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
