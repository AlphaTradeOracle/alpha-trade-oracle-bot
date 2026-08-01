import type { ReactNode } from 'react'
import type { CandleInterval } from '../../../services/marketData'
import { TimeframeSelector } from './TimeframeSelector'

interface ChartToolbarProps {
  title: string
  /** Secondary line next to the title, e.g. point count or last value */
  meta?: ReactNode
  busy?: boolean
  interval: CandleInterval
  onIntervalChange: (interval: CandleInterval) => void
  /** View controls rendered on the right */
  controls: ReactNode
  /** Extra row below the timeframe bar (filters, overlays …) */
  secondaryRow?: ReactNode
  /** Rendered next to the timeframe bar */
  trailing?: ReactNode
}

/** Chart header shared by the trade and equity charts. */
export function ChartToolbar({
  title,
  meta,
  busy = false,
  interval,
  onIntervalChange,
  controls,
  secondaryRow,
  trailing,
}: ChartToolbarProps) {
  return (
    <div className="flex flex-col gap-2.5 border-b border-[var(--color-border-subtle)] px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-baseline gap-2">
          <h3 className="text-sm font-semibold">{title}</h3>
          {meta ? (
            <span className="text-[11px] text-[var(--color-text-muted)]">{meta}</span>
          ) : null}
          {busy ? <span className="text-[11px] text-[var(--color-accent)]">lädt …</span> : null}
        </div>

        {controls}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <TimeframeSelector value={interval} onChange={onIntervalChange} disabled={busy} />
        {trailing}
      </div>

      {secondaryRow}
    </div>
  )
}
