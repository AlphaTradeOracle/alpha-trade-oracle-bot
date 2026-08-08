import { Clock3, Flag, PlayCircle } from 'lucide-react'
import type { Trade } from '../../../types/trade'
import { formatDateTime, formatDuration } from '../../../utils/format'
import { DetailCard } from './DetailField'

interface TradeTimelineProps {
  trade: Trade
}

/** Opened / closed timestamps and holding period. */
export function TradeTimeline({ trade }: TradeTimelineProps) {
  const rows = [
    { icon: PlayCircle, label: 'Opened', value: formatDateTime(trade.openedAt) },
    {
      icon: Flag,
      label: 'Closed',
      value: trade.closedAt ? formatDateTime(trade.closedAt) : 'Läuft noch',
    },
    {
      icon: Clock3,
      label: 'Duration',
      value: formatDuration(trade.openedAt, trade.closedAt),
    },
  ]

  return (
    <DetailCard title="Zeiten">
      <ul className="space-y-3">
        {rows.map(({ icon: Icon, label, value }) => (
          <li key={label} className="flex items-center gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]">
              <Icon size={15} strokeWidth={1.8} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[10px] uppercase tracking-[0.09em] text-[var(--color-text-muted)]">
                {label}
              </p>
              <p className="truncate text-sm tabular">{value}</p>
            </div>
          </li>
        ))}
      </ul>
    </DetailCard>
  )
}
