import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type AutoscaleInfo,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { Crosshair, Eye, EyeOff, Maximize2, RotateCcw } from 'lucide-react'
import type { Candle, Trade } from '../../types/trade'
import { formatPrice } from '../../utils/format'
import { Button } from '../ui/Button'

const COLORS = {
  up: '#3dcf8e',
  down: '#f07178',
  entry: '#4aa3ff',
  stop: '#f07178',
  tp: '#3dcf8e',
  grid: 'rgba(36, 48, 65, 0.55)',
  text: '#6d7f93',
}

interface TradeChartProps {
  trade: Trade
  candles: Candle[]
  loading?: boolean
  /** Chart body height in px */
  height?: number
}

interface HoverInfo {
  time: number
  open: number
  high: number
  low: number
  close: number
}

/**
 * Candlestick view of a single trade.
 *
 * Purely presentational: candles arrive via props, so switching from the mock
 * provider to a live exchange feed needs no changes here.
 */
export function TradeChart({ trade, candles, loading = false, height = 380 }: TradeChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const pathSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const priceLinesRef = useRef<IPriceLine[]>([])
  const markersRef = useRef<ReturnType<typeof createSeriesMarkers<Time>> | null>(null)

  const [showMarkers, setShowMarkers] = useState(true)
  const [hover, setHover] = useState<HoverInfo | null>(null)

  const openedTs = useMemo(
    () => Math.floor(new Date(trade.openedAt).getTime() / 1000) as UTCTimestamp,
    [trade.openedAt],
  )
  const closedTs = useMemo(
    () =>
      trade.closedAt
        ? (Math.floor(new Date(trade.closedAt).getTime() / 1000) as UTCTimestamp)
        : null,
    [trade.closedAt],
  )

  const isWin = (trade.realized ?? trade.upnl ?? 0) >= 0

  // Crypto pairs span many magnitudes; derive tick size from the entry price.
  const priceFormat = useMemo(() => {
    const ref = Math.abs(trade.entry)
    const precision = ref >= 100 ? 2 : ref >= 1 ? 4 : ref >= 0.01 ? 6 : 8
    return { precision, minMove: 10 ** -precision }
  }, [trade.entry])

  /* ---------------------------------------------------------------- setup */
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: COLORS.text,
        fontFamily: 'IBM Plex Mono, ui-monospace, monospace',
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: COLORS.grid, style: LineStyle.Dotted },
        horzLines: { color: COLORS.grid, style: LineStyle.Dotted },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#4aa3ff88', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#1a2330' },
        horzLine: { color: '#4aa3ff88', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#1a2330' },
      },
      rightPriceScale: { borderColor: 'rgba(36, 48, 65, 0.9)' },
      timeScale: {
        borderColor: 'rgba(36, 48, 65, 0.9)',
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: true,
      handleScale: true,
      autoSize: true,
    })

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: COLORS.up,
      downColor: COLORS.down,
      borderUpColor: COLORS.up,
      borderDownColor: COLORS.down,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
      priceLineVisible: false,
    })

    // Entry → exit connector, drawn above the candles.
    const pathSeries = chart.addSeries(LineSeries, {
      color: COLORS.entry,
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    })

    chart.subscribeCrosshairMove((param) => {
      const data = param.seriesData.get(candleSeries) as CandlestickData<Time> | undefined
      if (!data || param.time == null) {
        setHover(null)
        return
      }
      setHover({
        time: Number(param.time),
        open: data.open,
        high: data.high,
        low: data.low,
        close: data.close,
      })
    })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries
    pathSeriesRef.current = pathSeries

    return () => {
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      pathSeriesRef.current = null
      markersRef.current = null
      priceLinesRef.current = []
    }
  }, [])

  /* ----------------------------------------------------------------- data */
  useEffect(() => {
    const series = candleSeriesRef.current
    if (!series || candles.length === 0) return

    series.setData(
      candles.map((c) => ({
        time: c.time as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    )
    chartRef.current?.timeScale().fitContent()
  }, [candles])

  useEffect(() => {
    const series = candleSeriesRef.current
    if (!series) return

    // Price lines do not autoscale on their own — widen the range so entry,
    // stop and every take-profit stay inside the visible area.
    const levels = [
      trade.entry,
      trade.stop,
      ...(showMarkers ? (trade.takeProfits?.map((tp) => tp.price) ?? []) : []),
      ...(trade.exit != null ? [trade.exit] : []),
    ]

    series.applyOptions({
      priceFormat: { type: 'price', ...priceFormat },
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
        const res = original()
        if (!res?.priceRange || !showMarkers) return res
        return {
          ...res,
          priceRange: {
            minValue: Math.min(res.priceRange.minValue, ...levels),
            maxValue: Math.max(res.priceRange.maxValue, ...levels),
          },
        }
      },
    })

    pathSeriesRef.current?.applyOptions({
      priceFormat: { type: 'price', ...priceFormat },
    })
  }, [priceFormat, trade, showMarkers])

  /* ------------------------------------------------- trade visualisation */
  useEffect(() => {
    const series = candleSeriesRef.current
    const path = pathSeriesRef.current
    if (!series || !path || candles.length === 0) return

    // Reset previous overlays before redrawing.
    priceLinesRef.current.forEach((line) => series.removePriceLine(line))
    priceLinesRef.current = []
    path.setData([])
    markersRef.current?.setMarkers([])

    if (!showMarkers) return

    const addLine = (price: number, title: string, color: string, style: LineStyle) => {
      priceLinesRef.current.push(
        series.createPriceLine({
          price,
          color,
          lineWidth: 1,
          lineStyle: style,
          axisLabelVisible: true,
          title,
        }),
      )
    }

    addLine(trade.entry, 'Entry', COLORS.entry, LineStyle.Solid)
    addLine(trade.stop, 'Stop Loss', COLORS.stop, LineStyle.Dashed)
    trade.takeProfits?.forEach((tp) => {
      addLine(tp.price, tp.label, COLORS.tp, LineStyle.Dotted)
    })

    const markers: SeriesMarker<Time>[] = [
      {
        time: openedTs,
        position: trade.side === 'LONG' ? 'belowBar' : 'aboveBar',
        color: COLORS.entry,
        shape: trade.side === 'LONG' ? 'arrowUp' : 'arrowDown',
        text: `Entry ${formatPrice(trade.entry)}`,
      },
    ]

    if (closedTs && trade.exit != null) {
      markers.push({
        time: closedTs,
        position: trade.side === 'LONG' ? 'aboveBar' : 'belowBar',
        color: isWin ? COLORS.up : COLORS.down,
        shape: trade.side === 'LONG' ? 'arrowDown' : 'arrowUp',
        text: `Exit ${formatPrice(trade.exit)}`,
      })

      path.setData([
        { time: openedTs, value: trade.entry },
        { time: closedTs, value: trade.exit },
      ])
      path.applyOptions({ color: isWin ? COLORS.up : COLORS.down })
    }

    if (!markersRef.current) {
      markersRef.current = createSeriesMarkers(series, markers)
    } else {
      markersRef.current.setMarkers(markers)
    }
  }, [trade, candles, showMarkers, openedTs, closedTs, isWin])

  /* -------------------------------------------------------------- actions */
  const resetView = () => chartRef.current?.timeScale().fitContent()

  const centerOnTrade = () => {
    const timeScale = chartRef.current?.timeScale()
    if (!timeScale) return
    const end = closedTs ?? (candles[candles.length - 1]?.time as UTCTimestamp | undefined)
    if (!end) return
    const pad = Math.max((end - openedTs) * 0.4, 6 * 3600)
    timeScale.setVisibleRange({
      from: (openedTs - pad) as UTCTimestamp,
      to: (end + pad) as UTCTimestamp,
    })
  }

  return (
    <div className="panel overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border-subtle)] px-4 py-3">
        <div className="flex items-baseline gap-2">
          <h3 className="text-sm font-semibold">{trade.symbol}</h3>
          <span className="text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">
            1H · mock feed
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            variant="ghost"
            className="!px-2 !py-1.5 text-xs"
            onClick={() => setShowMarkers((v) => !v)}
            title={showMarkers ? 'Markierungen ausblenden' : 'Markierungen einblenden'}
          >
            {showMarkers ? <EyeOff size={14} /> : <Eye size={14} />}
            <span className="hidden sm:inline">Markierungen</span>
          </Button>
          <Button
            variant="ghost"
            className="!px-2 !py-1.5 text-xs"
            onClick={centerOnTrade}
            title="Auf Trade zentrieren"
          >
            <Crosshair size={14} />
            <span className="hidden sm:inline">Zentrieren</span>
          </Button>
          <Button
            variant="ghost"
            className="!px-2 !py-1.5 text-xs"
            onClick={resetView}
            title="Ansicht zurücksetzen"
          >
            <RotateCcw size={14} />
            <span className="hidden sm:inline">Reset</span>
          </Button>
        </div>
      </div>

      <div className="relative">
        <div ref={containerRef} style={{ height }} className="w-full" />

        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center bg-[var(--color-bg)]/50 text-xs text-[var(--color-text-muted)]">
            Lade Kerzen …
          </div>
        ) : null}

        {hover ? (
          <div className="pointer-events-none absolute left-3 top-3 rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-bg-elevated)]/95 px-3 py-2 text-[11px] tabular shadow-lg">
            <div className="mb-1 text-[var(--color-text-muted)]">
              {new Date(hover.time * 1000).toLocaleString('de-DE', {
                day: '2-digit',
                month: 'short',
                hour: '2-digit',
                minute: '2-digit',
              })}
            </div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
              <span className="text-[var(--color-text-muted)]">O</span>
              <span>{formatPrice(hover.open)}</span>
              <span className="text-[var(--color-text-muted)]">H</span>
              <span>{formatPrice(hover.high)}</span>
              <span className="text-[var(--color-text-muted)]">L</span>
              <span>{formatPrice(hover.low)}</span>
              <span className="text-[var(--color-text-muted)]">C</span>
              <span className={hover.close >= hover.open ? 'text-[var(--color-long)]' : 'text-[var(--color-short)]'}>
                {formatPrice(hover.close)}
              </span>
            </div>
          </div>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[var(--color-border-subtle)] px-4 py-2.5 text-[11px] text-[var(--color-text-muted)]">
        <Legend color={COLORS.entry} label="Entry" />
        <Legend color={COLORS.stop} label="Stop Loss" />
        <Legend color={COLORS.tp} label="Take Profit" />
        {closedTs ? <Legend color={isWin ? COLORS.up : COLORS.down} label="Exit" /> : null}
        <span className="ml-auto hidden items-center gap-1 sm:flex">
          <Maximize2 size={11} />
          Scrollen zum Zoomen · Ziehen zum Verschieben
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
