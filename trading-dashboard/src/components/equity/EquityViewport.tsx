import { useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import {
  AreaSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type LogicalRange,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { EquitySample } from '../../services/equityData'
import { EQUITY_OVERLAYS, type EquityOverlayId } from './EquityOverlays'

export interface EquityViewportHandle {
  /** Fit every loaded sample into view */
  resetView: () => void
  /** Jump back to the most recent window */
  showLatest: () => void
  /** Constrain the view to an absolute window */
  setRange: (from: number, to: number) => void
}

export interface EquityHover {
  time: number
  equity: number
  balance: number
  drawdownPct: number
}

interface EquityViewportProps {
  samples: EquitySample[]
  height: number
  overlays: EquityOverlayId[]
  autoScale: boolean
  onReachHistoryEdge?: () => void
  onHoverChange?: (hover: EquityHover | null) => void
  ref?: React.Ref<EquityViewportHandle>
}

/** Points of slack before the left edge triggers a history fetch. */
const HISTORY_TRIGGER_POINTS = 30

/** Samples framed when the chart opens. */
const LATEST_WINDOW_POINTS = 180

const colorOf = (id: EquityOverlayId) =>
  EQUITY_OVERLAYS.find((o) => o.id === id)?.color ?? '#4aa3ff'

/**
 * Thin wrapper around Lightweight Charts for the equity curve.
 *
 * Rendering only: samples arrive as props and every interaction is exposed
 * through the imperative handle, so the data layer and the toolbar stay
 * independent of the charting library.
 */
export function EquityViewport({
  samples,
  height,
  overlays,
  autoScale,
  onReachHistoryEdge,
  onHoverChange,
  ref,
}: EquityViewportProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const equityRef = useRef<ISeriesApi<'Area'> | null>(null)
  const balanceRef = useRef<ISeriesApi<'Line'> | null>(null)
  const drawdownRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const historyEdgeRef = useRef(onReachHistoryEdge)
  const hoverRef = useRef(onHoverChange)
  const didInitialFrameRef = useRef(false)

  const [ready, setReady] = useState(false)

  historyEdgeRef.current = onReachHistoryEdge
  hoverRef.current = onHoverChange

  /* --------------------------------------------------------------- setup */
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#6d7f93',
        fontFamily: 'IBM Plex Mono, ui-monospace, monospace',
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: 'rgba(36, 48, 65, 0.5)', style: LineStyle.Dotted },
        horzLines: { color: 'rgba(36, 48, 65, 0.5)', style: LineStyle.Dotted },
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
        scaleMargins: { top: 0.12, bottom: 0.18 },
      },
      timeScale: {
        borderColor: 'rgba(36, 48, 65, 0.9)',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      autoSize: true,
    })

    const equity = chart.addSeries(AreaSeries, {
      lineColor: colorOf('equity'),
      lineWidth: 2,
      topColor: 'rgba(74, 163, 255, 0.22)',
      bottomColor: 'rgba(74, 163, 255, 0)',
      priceLineVisible: false,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })

    const balance = chart.addSeries(LineSeries, {
      color: colorOf('balance'),
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      visible: false,
    })

    // Drawdown lives on its own scale so percentages never distort the equity.
    // Bars hang from zero, which reads correctly for negative values.
    const drawdown = chart.addSeries(HistogramSeries, {
      color: 'rgba(240, 113, 120, 0.55)',
      base: 0,
      priceScaleId: 'drawdown',
      priceLineVisible: false,
      lastValueVisible: false,
      visible: false,
      priceFormat: { type: 'percent', precision: 1, minMove: 0.1 },
    })
    chart.priceScale('drawdown').applyOptions({
      scaleMargins: { top: 0.68, bottom: 0 },
      visible: false,
    })

    chart.subscribeCrosshairMove((param) => {
      const point = param.seriesData.get(equity) as LineData<Time> | undefined
      if (!point || param.time == null) {
        hoverRef.current?.(null)
        return
      }
      const bal = param.seriesData.get(balance) as LineData<Time> | undefined
      const dd = param.seriesData.get(drawdown) as LineData<Time> | undefined
      hoverRef.current?.({
        time: Number(param.time),
        equity: point.value,
        balance: bal?.value ?? point.value,
        drawdownPct: dd?.value ?? 0,
      })
    })

    chart.timeScale().subscribeVisibleLogicalRangeChange((range: LogicalRange | null) => {
      if (!range || !didInitialFrameRef.current) return
      if (range.from < HISTORY_TRIGGER_POINTS) historyEdgeRef.current?.()
    })

    chartRef.current = chart
    equityRef.current = equity
    balanceRef.current = balance
    drawdownRef.current = drawdown
    setReady(true)

    return () => {
      chart.remove()
      chartRef.current = null
      equityRef.current = null
      balanceRef.current = null
      drawdownRef.current = null
      setReady(false)
    }
  }, [])

  /* ---------------------------------------------------------------- data */
  useEffect(() => {
    const chart = chartRef.current
    const equity = equityRef.current
    if (!chart || !equity || samples.length === 0) return

    // Prepending history shifts every logical index, so restore the exact
    // time window the user was looking at.
    const previousRange = didInitialFrameRef.current
      ? chart.timeScale().getVisibleRange()
      : null

    equity.setData(
      samples.map((s) => ({ time: s.time as UTCTimestamp, value: s.equity })),
    )
    balanceRef.current?.setData(
      samples.map((s) => ({ time: s.time as UTCTimestamp, value: s.balance })),
    )
    drawdownRef.current?.setData(
      samples.map((s) => ({ time: s.time as UTCTimestamp, value: s.drawdownPct })),
    )

    if (previousRange) chart.timeScale().setVisibleRange(previousRange)
  }, [samples])

  /* ------------------------------------------------------------ overlays */
  useEffect(() => {
    balanceRef.current?.applyOptions({ visible: overlays.includes('balance') })
    drawdownRef.current?.applyOptions({ visible: overlays.includes('drawdown') })
    equityRef.current?.applyOptions({ visible: overlays.includes('equity') })
    chartRef.current?.priceScale('drawdown').applyOptions({
      visible: overlays.includes('drawdown'),
    })
  }, [overlays])

  useEffect(() => {
    chartRef.current?.priceScale('right').applyOptions({ autoScale })
  }, [autoScale])

  /* ------------------------------------------------------------- actions */
  const resetView = useCallback(() => {
    chartRef.current?.timeScale().fitContent()
  }, [])

  const showLatest = useCallback(() => {
    const timeScale = chartRef.current?.timeScale()
    if (!timeScale || samples.length === 0) return
    const last = samples[samples.length - 1].time
    const index = Math.max(samples.length - LATEST_WINDOW_POINTS, 0)
    timeScale.setVisibleRange({
      from: samples[index].time as UTCTimestamp,
      to: last as UTCTimestamp,
    })
  }, [samples])

  const setRange = useCallback(
    (from: number, to: number) => {
      const timeScale = chartRef.current?.timeScale()
      if (!timeScale || samples.length === 0) return
      const first = samples[0].time
      const last = samples[samples.length - 1].time
      timeScale.setVisibleRange({
        from: Math.max(from, first) as UTCTimestamp,
        to: Math.min(to, last) as UTCTimestamp,
      })
    },
    [samples],
  )

  useImperativeHandle(ref, () => ({ resetView, showLatest, setRange }), [
    resetView,
    showLatest,
    setRange,
  ])

  /* The loaded window already equals the selected period, so show all of it. */
  useEffect(() => {
    if (!ready || samples.length === 0 || didInitialFrameRef.current) return
    didInitialFrameRef.current = true
    resetView()
  }, [ready, samples.length, resetView])

  return (
    <div
      ref={containerRef}
      style={{ height }}
      onDoubleClick={showLatest}
      className="w-full cursor-crosshair"
    />
  )
}
