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
  const tip = getKpiTooltip('Performance')

  const card = (
    <article className="panel relative flex min-h-[108px] w-full flex-col items-center justify-center gap-2 px-4 pb-3.5 pt-4 text-center transition-colors hover:bg-[var(--color-surface-hover)]">
      <span
        className="absolute right-3 top-3 rounded-lg bg-[var(--color-surface-hover)] p-1.5 text-[var(--color-text-secondary)]"
        aria-hidden
      >
        <Gauge size={15} strokeWidth={1.8} />
      </span>
      <p className="w-full px-6 text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
        Performance
      </p>
      <dl className="grid w-full grid-cols-2 gap-x-2 gap-y-2">
        {windows.map((win) => (
          <div key={win.label} className="flex flex-col items-center gap-0.5">
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

  if (!tip) return card
  return <Tooltip content={tip}>{card}</Tooltip>
}
