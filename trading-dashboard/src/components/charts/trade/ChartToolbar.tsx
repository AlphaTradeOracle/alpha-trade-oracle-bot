import type { CandleInterval } from '../../../services/marketData'
import { ChartControls } from './ChartControls'
import { TimeframeSelector } from './TimeframeSelector'

interface ChartToolbarProps {
  symbol: string
  interval: CandleInterval
  onIntervalChange: (interval: CandleInterval) => void
  /** Number of candles currently in memory */
  barCount: number
  busy?: boolean
  showMarkers: boolean
  onToggleMarkers: () => void
  autoScale: boolean
  onToggleAutoScale: () => void
  onCenter: () => void
  onReset: () => void
}

/** Header above the chart: symbol, timeframes and view controls. */
export function ChartToolbar({
  symbol,
  interval,
  onIntervalChange,
  barCount,
  busy = false,
  ...controls
}: ChartToolbarProps) {
  return (
    <div className="flex flex-col gap-2.5 border-b border-[var(--color-border-subtle)] px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <h3 className="text-sm font-semibold">{symbol}</h3>
          <span className="text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">
            mock feed · {barCount.toLocaleString('de-DE')} Kerzen
          </span>
          {busy ? (
            <span className="text-[11px] text-[var(--color-accent)]">lädt …</span>
          ) : null}
        </div>

        <ChartControls {...controls} />
      </div>

      <TimeframeSelector value={interval} onChange={onIntervalChange} disabled={busy} />
    </div>
  )
}
