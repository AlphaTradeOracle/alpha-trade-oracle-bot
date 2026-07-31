import type { Trade } from '../../types/trade'
import { formatMoney, formatPrice, formatSince } from '../../utils/format'
import { EmptyState } from '../ui/EmptyState'
import { PnLCell } from './PnLCell'
import { ScoreBadge } from './ScoreBadge'
import { SideBadge } from './SideBadge'

interface OpenTradesTableProps {
  trades: Trade[]
}

export function OpenTradesTable({ trades }: OpenTradesTableProps) {
  if (trades.length === 0) {
    return (
      <div className="panel">
        <EmptyState title="No open trades" description="Adjust filters or wait for the next fill." />
      </div>
    )
  }

  return (
    <div className="panel overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[960px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border-subtle)] text-[11px] uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
              {[
                'Symbol',
                'Side',
                'Entry',
                'Mark Price',
                'Stop',
                'uPnL',
                'R',
                'Margin',
                'Score',
                'Opened Since',
              ].map((h) => (
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
                className="border-b border-[var(--color-border-subtle)]/80 transition-colors last:border-0 hover:bg-[var(--color-surface-hover)]/70"
              >
                <td className="px-4 py-3 font-medium whitespace-nowrap">{t.symbol}</td>
                <td className="px-4 py-3">
                  <SideBadge side={t.side} />
                </td>
                <td className="px-4 py-3 tabular text-[var(--color-text-secondary)]">
                  {formatPrice(t.entry)}
                </td>
                <td className="px-4 py-3 tabular">{formatPrice(t.mark)}</td>
                <td className="px-4 py-3 tabular text-[var(--color-text-secondary)]">
                  {formatPrice(t.stop)}
                </td>
                <td className="px-4 py-3">
                  <PnLCell value={t.upnl} />
                </td>
                <td className="px-4 py-3">
                  <PnLCell value={t.r} asR />
                </td>
                <td className="px-4 py-3 tabular text-[var(--color-text-secondary)]">
                  {formatMoney(t.margin)}
                </td>
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
