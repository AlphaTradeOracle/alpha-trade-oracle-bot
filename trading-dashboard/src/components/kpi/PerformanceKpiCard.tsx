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

function cellTone(pct: number | null): {
  value: string
  chip: string
} {
  if (pct == null || pct === 0) {
    return {
      value: 'text-[var(--color-text-secondary)]',
      chip: 'bg-[var(--color-surface-hover)]/70',
    }
  }
  if (pct > 0) {
    return {
      value: 'text-[var(--color-long)]',
      chip: 'bg-[var(--color-long-soft)]',
    }
  }
  return {
    value: 'text-[var(--color-short)]',
    chip: 'bg-[var(--color-short-soft)]',
  }
}

function formatWindowPct(pct: number | null): string {
  if (pct == null) return '—'
  return formatPct(pct)
}

function WindowCell({ win }: { win: PerformanceWindow }) {
  const tone = cellTone(win.pct)
  return (
    <div
      className={`flex h-full min-h-0 flex-col items-center justify-center gap-1 rounded-lg px-1.5 ${tone.chip}`}
    >
      <dt className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-muted)]">
        {win.label}
      </dt>
      <dd className={`tabular text-[15px] font-semibold leading-none tracking-tight ${tone.value}`}>
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
    <article className="panel relative flex h-[108px] w-full flex-col px-2.5 pb-2 pt-2 text-center transition-colors hover:bg-[var(--color-surface-hover)]">
      <span
        className="absolute right-2.5 top-2 rounded-lg bg-[var(--color-surface-hover)] p-1.5 text-[var(--color-text-secondary)]"
        aria-hidden
      >
        <Gauge size={15} strokeWidth={1.8} />
      </span>
      <p className="w-full shrink-0 px-7 text-center text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
        Performance
      </p>
      <dl className="mt-1.5 grid min-h-0 flex-1 grid-cols-2 grid-rows-2 gap-1.5">
        {windows.map((win) => (
          <WindowCell key={win.label} win={win} />
        ))}
      </dl>
    </article>
  )

  if (!tip) return card
  return <Tooltip content={tip}>{card}</Tooltip>
}
