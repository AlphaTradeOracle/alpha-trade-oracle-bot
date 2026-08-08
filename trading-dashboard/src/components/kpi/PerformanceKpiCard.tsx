import { Gauge } from 'lucide-react'
import { getKpiTooltip } from '../../config/kpiTooltips'
import { formatPct } from '../../utils/format'
import {
  computePerformanceWindows,
  type PerformanceWindow,
} from '../../utils/performanceWindows'
import type { EquityPoint } from '../../types/trade'
import { Tooltip } from '../ui/Tooltip'

interface PerformanceKpiCardProps {
  equity: EquityPoint[]
  loading?: boolean
}

function valueTone(pct: number | null): string {
  if (pct == null || pct === 0) return 'text-[var(--color-text-secondary)]'
  if (pct > 0) return 'text-[var(--color-long)]'
  return 'text-[var(--color-short)]'
}

function formatWindowPct(pct: number | null): string {
  if (pct == null) return '—'
  return formatPct(pct)
}

function WindowCell({ win }: { win: PerformanceWindow }) {
  return (
    <div className="flex flex-col items-center gap-1">
      <dt className="text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
        {win.label}
      </dt>
      <dd className={`tabular text-sm font-semibold leading-none tracking-tight ${valueTone(win.pct)}`}>
        {formatWindowPct(win.pct)}
      </dd>
    </div>
  )
}

export function PerformanceKpiCard({ equity, loading = false }: PerformanceKpiCardProps) {
  const windows: PerformanceWindow[] = loading
    ? [
        { label: '1h', pct: null },
        { label: '24h', pct: null },
        { label: '7D', pct: null },
        { label: '30D', pct: null },
      ]
    : computePerformanceWindows(equity)
  const tip = getKpiTooltip('Performance')

  const card = (
    <article className="panel relative flex h-[108px] w-full flex-col px-3 pb-2 pt-2 text-center transition-colors hover:bg-[var(--color-surface-hover)]">
      <span
        className="absolute right-2.5 top-2 rounded-lg bg-[var(--color-surface-hover)] p-1.5 text-[var(--color-text-secondary)]"
        aria-hidden
      >
        <Gauge size={15} strokeWidth={1.8} />
      </span>
      <p className="w-full shrink-0 px-7 text-center text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
        Performance
      </p>
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <dl className="grid w-full max-w-[11.5rem] grid-cols-2 gap-x-5 gap-y-2.5">
          {windows.map((win) => (
            <WindowCell key={win.label} win={win} />
          ))}
        </dl>
      </div>
    </article>
  )

  if (!tip) return card
  return <Tooltip content={tip}>{card}</Tooltip>
}
