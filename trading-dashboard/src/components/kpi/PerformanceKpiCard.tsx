import { Gauge } from 'lucide-react'
import { formatPct } from '../../utils/format'
import {
  computePerformanceWindows,
  type PerformanceWindow,
} from '../../utils/performanceWindows'
import type { EquityPoint } from '../../types/trade'

interface PerformanceKpiCardProps {
  equity: EquityPoint[]
}

function toneClass(pct: number | null): string {
  if (pct == null || pct === 0) return 'text-[var(--color-text-muted)]'
  if (pct > 0) return 'text-[var(--color-long)]'
  return 'text-[var(--color-short)]'
}

function formatWindowPct(pct: number | null): string {
  if (pct == null) return '—'
  return formatPct(pct)
}

export function PerformanceKpiCard({ equity }: PerformanceKpiCardProps) {
  const windows: PerformanceWindow[] = computePerformanceWindows(equity)

  return (
    <article className="panel flex min-h-[108px] w-full flex-col justify-between gap-2 p-4 text-left">
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
          Performance
        </p>
        <span className="rounded-lg bg-[var(--color-surface-hover)] p-1.5 text-[var(--color-text-secondary)]">
          <Gauge size={15} strokeWidth={1.8} />
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5">
        {windows.map((win) => (
          <div key={win.label} className="flex items-baseline justify-between gap-2">
            <dt className="text-[10px] font-medium uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
              {win.label}
            </dt>
            <dd className={`tabular text-xs font-semibold sm:text-[0.8rem] ${toneClass(win.pct)}`}>
              {formatWindowPct(win.pct)}
            </dd>
          </div>
        ))}
      </dl>
    </article>
  )
}
