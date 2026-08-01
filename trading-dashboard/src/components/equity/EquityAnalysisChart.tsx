import { useRef, useState } from 'react'
import { Maximize2 } from 'lucide-react'
import { useEquitySeries } from '../../hooks/useEquitySeries'
import type { EquitySample } from '../../services/equityData'
import type { PortfolioSnapshot } from '../../types/trade'
import { formatMoney, formatPct, formatTimestamp } from '../../utils/format'
import { ChartControls, ChartToolbar } from '../charts/shared'
import {
  DEFAULT_EQUITY_RANGE,
  EquityFilters,
  type EquityRangeId,
} from './EquityFilters'
import { EquityOverlayMenu } from './EquityOverlayMenu'
import { DEFAULT_OVERLAYS, type EquityOverlayId } from './EquityOverlays'
import { EquityViewport, type EquityHover, type EquityViewportHandle } from './EquityViewport'

interface EquityAnalysisChartProps {
  portfolio: PortfolioSnapshot
  height?: number
  /** Reported upwards so the statistics panel shares the loaded window */
  onSamplesChange?: (samples: EquitySample[]) => void
}

/**
 * Interactive equity chart.
 *
 * Composes the shared toolbar, the period and overlay selectors and the
 * charting viewport. Data access lives in `useEquitySeries`, so a live source
 * can be introduced without touching presentation code.
 */
export function EquityAnalysisChart({
  portfolio,
  height = 420,
  onSamplesChange,
}: EquityAnalysisChartProps) {
  const [range, setRange] = useState<EquityRangeId>(DEFAULT_EQUITY_RANGE)
  const [overlays, setOverlays] = useState<EquityOverlayId[]>(DEFAULT_OVERLAYS)
  const [autoScale, setAutoScale] = useState(true)
  const [hover, setHover] = useState<EquityHover | null>(null)

  const viewportRef = useRef<EquityViewportHandle | null>(null)
  const reportedRef = useRef<EquitySample[] | null>(null)

  const { samples, loading, loadingHistory, exhausted, error, loadOlder } = useEquitySeries(
    portfolio,
    range,
  )

  if (samples !== reportedRef.current) {
    reportedRef.current = samples
    onSamplesChange?.(samples)
  }

  const latest = samples[samples.length - 1]
  const first = samples[0]
  const changePct =
    first && latest && first.equity > 0
      ? ((latest.equity - first.equity) / first.equity) * 100
      : 0

  const toggleOverlay = (id: EquityOverlayId) =>
    setOverlays((current) =>
      current.includes(id) ? current.filter((o) => o !== id) : [...current, id],
    )

  return (
    <div className="panel overflow-hidden">
      <ChartToolbar
        title="Equity Curve"
        meta={
          latest ? (
            <span className="tabular">
              {formatMoney(latest.equity)}{' '}
              <span
                className={
                  changePct >= 0 ? 'text-[var(--color-long)]' : 'text-[var(--color-short)]'
                }
              >
                {formatPct(changePct)}
              </span>
            </span>
          ) : null
        }
        busy={loading || loadingHistory}
        leading={
          <EquityFilters value={range} onChange={setRange} disabled={loading} />
        }
        controls={
          <ChartControls
            autoScale={{ active: autoScale, onToggle: () => setAutoScale((v) => !v) }}
            onCenter={() => viewportRef.current?.showLatest()}
            centerLabel="Aktuell"
            onReset={() => viewportRef.current?.resetView()}
          />
        }
        secondaryRow={<EquityOverlayMenu active={overlays} onToggle={toggleOverlay} />}
      />

      <div className="relative">
        <EquityViewport
          key={range}
          ref={viewportRef}
          samples={samples}
          height={height}
          overlays={overlays}
          autoScale={autoScale}
          onReachHistoryEdge={loadOlder}
          onHoverChange={setHover}
        />

        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center bg-[var(--color-bg)]/50 text-xs text-[var(--color-text-muted)]">
            Lade Equity …
          </div>
        ) : null}

        {error ? (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-[var(--color-short)]">
            {error}
          </div>
        ) : null}

        {loadingHistory ? (
          <div className="pointer-events-none absolute bottom-3 left-3 rounded-md border border-[var(--color-border-subtle)] bg-[var(--color-bg-elevated)]/95 px-2 py-1 text-[10px] text-[var(--color-text-muted)]">
            Historie wird geladen …
          </div>
        ) : null}

        {hover ? (
          <div className="pointer-events-none absolute left-3 top-3 rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-bg-elevated)]/95 px-3 py-2 text-[11px] tabular shadow-lg">
            <div className="mb-1 text-[var(--color-text-muted)]">
              {formatTimestamp(hover.time)}
            </div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
              <span className="text-[var(--color-text-muted)]">Equity</span>
              <span>{formatMoney(hover.equity)}</span>
              {overlays.includes('balance') ? (
                <>
                  <span className="text-[var(--color-text-muted)]">Balance</span>
                  <span>{formatMoney(hover.balance)}</span>
                </>
              ) : null}
              {overlays.includes('drawdown') ? (
                <>
                  <span className="text-[var(--color-text-muted)]">Drawdown</span>
                  <span className="text-[var(--color-short)]">
                    {hover.drawdownPct.toFixed(2)}%
                  </span>
                </>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[var(--color-border-subtle)] px-4 py-2.5 text-[11px] text-[var(--color-text-muted)]">
        <span>{samples.length.toLocaleString('de-DE')} Datenpunkte</span>
        {exhausted ? <span>Beginn der Historie erreicht</span> : null}
        <span className="ml-auto hidden items-center gap-1 lg:flex">
          <Maximize2 size={11} />
          Mausrad zoomen · Ziehen verschieben · Doppelklick zeigt den aktuellen Bereich
        </span>
      </div>
    </div>
  )
}
