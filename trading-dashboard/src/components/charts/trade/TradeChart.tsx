import { useRef, useState } from 'react'
import { Maximize2 } from 'lucide-react'
import { useTradeCandles } from '../../../hooks/useTradeCandles'
import type { CandleInterval } from '../../../services/marketData'
import type { Trade } from '../../../types/trade'
import { formatPrice, formatTimestamp } from '../../../utils/format'
import { ChartControls, ChartToolbar } from '../shared'
import {
  CHART_COLORS,
  ChartViewport,
  type ChartViewportHandle,
  type OhlcHover,
} from './ChartViewport'

interface TradeChartProps {
  trade: Trade
  /** Chart body height in px */
  height?: number
  defaultInterval?: CandleInterval
}

/**
 * Trade chart shell.
 *
 * Composes the toolbar, the charting viewport and the candle feed. Data access
 * lives in `useTradeCandles`, so live sources can be introduced without
 * touching any presentation code.
 */
export function TradeChart({ trade, height = 420, defaultInterval = '1h' }: TradeChartProps) {
  const [interval, setInterval] = useState<CandleInterval>(defaultInterval)
  const [showMarkers, setShowMarkers] = useState(true)
  const [autoScale, setAutoScale] = useState(true)
  const [hover, setHover] = useState<OhlcHover | null>(null)

  const viewportRef = useRef<ChartViewportHandle | null>(null)
  const { candles, loading, loadingHistory, exhausted, error, loadOlder } = useTradeCandles(
    trade,
    interval,
  )

  const isWin = (trade.realized ?? trade.upnl ?? 0) >= 0

  return (
    <div className="panel overflow-hidden">
      <ChartToolbar
        title={trade.symbol}
        meta={`${candles.length.toLocaleString('de-DE')} Kerzen`}
        interval={interval}
        onIntervalChange={setInterval}
        busy={loading || loadingHistory}
        controls={
          <ChartControls
            markers={{
              active: showMarkers,
              onToggle: () => setShowMarkers((v) => !v),
              label: 'Markierungen',
            }}
            autoScale={{ active: autoScale, onToggle: () => setAutoScale((v) => !v) }}
            onCenter={() => viewportRef.current?.centerOnTrade()}
            onReset={() => viewportRef.current?.resetView()}
          />
        }
      />

      <div className="relative">
        <ChartViewport
          key={`${trade.id}-${interval}`}
          ref={viewportRef}
          trade={trade}
          candles={candles}
          height={height}
          showMarkers={showMarkers}
          autoScale={autoScale}
          onReachHistoryEdge={loadOlder}
          onHoverChange={setHover}
        />

        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center bg-[var(--color-bg)]/50 text-xs text-[var(--color-text-muted)]">
            Lade Kerzen …
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
              <span className="text-[var(--color-text-muted)]">O</span>
              <span>{formatPrice(hover.open)}</span>
              <span className="text-[var(--color-text-muted)]">H</span>
              <span>{formatPrice(hover.high)}</span>
              <span className="text-[var(--color-text-muted)]">L</span>
              <span>{formatPrice(hover.low)}</span>
              <span className="text-[var(--color-text-muted)]">C</span>
              <span
                className={
                  hover.close >= hover.open
                    ? 'text-[var(--color-long)]'
                    : 'text-[var(--color-short)]'
                }
              >
                {formatPrice(hover.close)}
              </span>
            </div>
          </div>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[var(--color-border-subtle)] px-4 py-2.5 text-[11px] text-[var(--color-text-muted)]">
        <Legend color={CHART_COLORS.entry} label="Entry" />
        <Legend color={CHART_COLORS.stop} label="Stop Loss" />
        <Legend color={CHART_COLORS.tp} label="Take Profit" />
        {trade.closedAt ? (
          <Legend color={isWin ? CHART_COLORS.up : CHART_COLORS.down} label="Exit" />
        ) : null}
        {exhausted ? <span>Beginn der Historie erreicht</span> : null}
        <span className="ml-auto hidden items-center gap-1 lg:flex">
          <Maximize2 size={11} />
          Mausrad zoomen · Ziehen verschieben · Doppelklick zentriert
        </span>
      </div>
    </div>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="h-0.5 w-4 rounded-full" style={{ background: color }} />
      {label}
    </span>
  )
}
