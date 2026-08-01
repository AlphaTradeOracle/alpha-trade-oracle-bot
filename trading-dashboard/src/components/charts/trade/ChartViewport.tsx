import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
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
  type ISeriesMarkersPluginApi,
  type LogicalRange,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { Candle, Trade } from '../../../types/trade'
import { formatPrice } from '../../../utils/format'

export const CHART_COLORS = {
  up: '#3dcf8e',
  down: '#f07178',
  entry: '#4aa3ff',
  stop: '#f07178',
  tp: '#3dcf8e',
  grid: 'rgba(36, 48, 65, 0.5)',
  text: '#6d7f93',
} as const

/** Imperative handle so the toolbar can drive the viewport. */
export interface ChartViewportHandle {
  /** Fit every loaded candle into view */
  resetView: () => void
  /** Frame entry, exit and all levels of the trade */
  centerOnTrade: () => void
}

export interface OhlcHover {
  time: number
  open: number
  high: number
  low: number
  close: number
}

interface ChartViewportProps {
  trade: Trade
  candles: Candle[]
  height: number
  showMarkers: boolean
  autoScale: boolean
  /** Called when the user pans close to the oldest loaded candle */
  onReachHistoryEdge?: () => void
  onHoverChange?: (hover: OhlcHover | null) => void
  ref?: React.Ref<ChartViewportHandle>
}

/** Bars of slack before the left edge triggers a history fetch. */
const HISTORY_TRIGGER_BARS = 30

/** Keeps short trades readable on coarse timeframes. */
const MIN_VISIBLE_BARS = 70

/**
 * Thin wrapper around Lightweight Charts.
 *
 * Owns nothing but rendering: candles arrive as props and every interaction is
 * exposed through the imperative handle, which keeps the toolbar and the data
 * layer independent of the charting library.
 */
export function ChartViewport({
  trade,
  candles,
  height,
  showMarkers,
  autoScale,
  onReachHistoryEdge,
  onHoverChange,
  ref,
}: ChartViewportProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const pathSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const priceLinesRef = useRef<IPriceLine[]>([])
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const historyEdgeRef = useRef(onReachHistoryEdge)
  const hoverRef = useRef(onHoverChange)
  const didInitialFrameRef = useRef(false)

  const [ready, setReady] = useState(false)

  historyEdgeRef.current = onReachHistoryEdge
  hoverRef.current = onHoverChange

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
    const reference = Math.abs(trade.entry)
    const precision = reference >= 100 ? 2 : reference >= 1 ? 4 : reference >= 0.01 ? 6 : 8
    return { precision, minMove: 10 ** -precision }
  }, [trade.entry])

  const tradeLevels = useMemo(() => {
    const levels = [trade.entry, trade.stop]
    trade.takeProfits?.forEach((tp) => levels.push(tp.price))
    if (trade.exit != null) levels.push(trade.exit)
    return levels
  }, [trade])

  /* --------------------------------------------------------------- setup */
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: CHART_COLORS.text,
        fontFamily: 'IBM Plex Mono, ui-monospace, monospace',
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: CHART_COLORS.grid, style: LineStyle.Dotted },
        horzLines: { color: CHART_COLORS.grid, style: LineStyle.Dotted },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: '#4aa3ff88',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#1a2330',
        },
        horzLine: {
          color: '#4aa3ff88',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#1a2330',
        },
      },
      rightPriceScale: {
        borderColor: 'rgba(36, 48, 65, 0.9)',
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderColor: 'rgba(36, 48, 65, 0.9)',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      autoSize: true,
    })

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: CHART_COLORS.up,
      downColor: CHART_COLORS.down,
      borderUpColor: CHART_COLORS.up,
      borderDownColor: CHART_COLORS.down,
      wickUpColor: CHART_COLORS.up,
      wickDownColor: CHART_COLORS.down,
      priceLineVisible: false,
    })

    // Entry → exit connector drawn above the candles.
    const pathSeries = chart.addSeries(LineSeries, {
      color: CHART_COLORS.entry,
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    })

    chart.subscribeCrosshairMove((param) => {
      const data = param.seriesData.get(candleSeries) as CandlestickData<Time> | undefined
      if (!data || param.time == null) {
        hoverRef.current?.(null)
        return
      }
      hoverRef.current?.({
        time: Number(param.time),
        open: data.open,
        high: data.high,
        low: data.low,
        close: data.close,
      })
    })

    // Panning near the left edge pulls in more history — only once the initial
    // framing has settled, otherwise the first render would trigger a fetch.
    chart.timeScale().subscribeVisibleLogicalRangeChange((range: LogicalRange | null) => {
      if (!range || !didInitialFrameRef.current) return
      if (range.from < HISTORY_TRIGGER_BARS) historyEdgeRef.current?.()
    })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries
    pathSeriesRef.current = pathSeries
    setReady(true)

    return () => {
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      pathSeriesRef.current = null
      markersRef.current = null
      priceLinesRef.current = []
      setReady(false)
    }
  }, [])

  /* ---------------------------------------------------------------- data */
  useEffect(() => {
    const series = candleSeriesRef.current
    const chart = chartRef.current
    if (!series || !chart || candles.length === 0) return

    // Prepending history shifts every logical index, so remember where the
    // user was looking and restore that exact time window afterwards.
    const previousRange = didInitialFrameRef.current
      ? chart.timeScale().getVisibleRange()
      : null

    series.setData(
      candles.map((c) => ({
        time: c.time as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    )

    if (previousRange) {
      chart.timeScale().setVisibleRange(previousRange)
    }
  }, [candles])

  /* ------------------------------------------------------ scale & levels */
  useEffect(() => {
    const series = candleSeriesRef.current
    if (!series) return

    series.applyOptions({
      priceFormat: { type: 'price', ...priceFormat },
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
        const base = original()
        // Price lines do not autoscale on their own — widen the range so entry,
        // stop and every take-profit stay inside the visible area.
        if (!base?.priceRange || !showMarkers || !autoScale) return base
        return {
          ...base,
          priceRange: {
            minValue: Math.min(base.priceRange.minValue, ...tradeLevels),
            maxValue: Math.max(base.priceRange.maxValue, ...tradeLevels),
          },
        }
      },
    })

    pathSeriesRef.current?.applyOptions({ priceFormat: { type: 'price', ...priceFormat } })
    chartRef.current?.priceScale('right').applyOptions({ autoScale })
  }, [priceFormat, showMarkers, autoScale, tradeLevels])

  /* ---------------------------------------------- trade visualisation */
  useEffect(() => {
    const series = candleSeriesRef.current
    const path = pathSeriesRef.current
    if (!series || !path) return

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

    addLine(trade.entry, 'Entry', CHART_COLORS.entry, LineStyle.Solid)
    addLine(trade.stop, 'Stop Loss', CHART_COLORS.stop, LineStyle.Dashed)
    trade.takeProfits?.forEach((tp) => addLine(tp.price, tp.label, CHART_COLORS.tp, LineStyle.Dotted))

    const markers: SeriesMarker<Time>[] = [
      {
        time: openedTs,
        position: trade.side === 'LONG' ? 'belowBar' : 'aboveBar',
        color: CHART_COLORS.entry,
        shape: trade.side === 'LONG' ? 'arrowUp' : 'arrowDown',
        text: `Entry ${formatPrice(trade.entry)}`,
      },
    ]

    if (closedTs && trade.exit != null) {
      markers.push({
        time: closedTs,
        position: trade.side === 'LONG' ? 'aboveBar' : 'belowBar',
        color: isWin ? CHART_COLORS.up : CHART_COLORS.down,
        shape: trade.side === 'LONG' ? 'arrowDown' : 'arrowUp',
        text: `Exit ${formatPrice(trade.exit)}`,
      })

      path.setData([
        { time: openedTs, value: trade.entry },
        { time: closedTs, value: trade.exit },
      ])
      path.applyOptions({ color: isWin ? CHART_COLORS.up : CHART_COLORS.down })
    }

    if (!markersRef.current) {
      markersRef.current = createSeriesMarkers(series, markers)
    } else {
      markersRef.current.setMarkers(markers)
    }
  }, [trade, showMarkers, openedTs, closedTs, isWin, candles.length])

  /* ------------------------------------------------------------ actions */
  const resetView = useCallback(() => {
    chartRef.current?.timeScale().fitContent()
  }, [])

  const centerOnTrade = useCallback(() => {
    const timeScale = chartRef.current?.timeScale()
    if (!timeScale || candles.length === 0) return

    const firstCandle = candles[0].time
    const lastCandle = candles[candles.length - 1].time as UTCTimestamp
    // Derive the bar width from the data so every timeframe keeps a readable
    // number of candles on screen instead of zooming into a handful of bars.
    const step = candles.length > 1 ? candles[1].time - candles[0].time : 3600
    const minSpan = step * MIN_VISIBLE_BARS

    const end = closedTs ?? lastCandle
    const tradeSpan = Math.max(end - openedTs, step)
    const span = Math.max(tradeSpan, minSpan)
    const center = openedTs + tradeSpan / 2

    timeScale.setVisibleRange({
      from: Math.max(center - span / 2, firstCandle) as UTCTimestamp,
      to: Math.min(center + span / 2, lastCandle) as UTCTimestamp,
    })
  }, [candles, closedTs, openedTs])

  useImperativeHandle(ref, () => ({ resetView, centerOnTrade }), [resetView, centerOnTrade])

  /* Frame the trade once the first page has arrived. */
  useEffect(() => {
    if (!ready || candles.length === 0 || didInitialFrameRef.current) return
    didInitialFrameRef.current = true
    centerOnTrade()
  }, [ready, candles.length, centerOnTrade])

  /* Re-frame when the timeframe changes (candle identity resets). */
  useEffect(() => {
    didInitialFrameRef.current = false
  }, [trade.id])

  return (
    <div
      ref={containerRef}
      style={{ height }}
      onDoubleClick={centerOnTrade}
      className="w-full cursor-crosshair"
    />
  )
}
