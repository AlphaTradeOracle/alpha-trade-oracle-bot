import { useEffect, useMemo, useState } from 'react'
import { createMockMarketData } from '../services/marketData'
import { INTERVAL_SECONDS, type CandleInterval } from '../services/marketData/types'
import type { Candle, Trade } from '../types/trade'

const DEFAULT_WINDOW_DAYS = 3
const MIN_BARS = 48

interface UseTradeCandlesResult {
  candles: Candle[]
  interval: CandleInterval
  loading: boolean
  error: string | null
}

/**
 * Loads the candle series backing a trade's chart.
 *
 * The window always covers the full trade: it starts at three days by default
 * and widens automatically when the position ran longer, so entry and exit stay
 * visible. Data comes from the mock provider today; swapping in an exchange
 * adapter keeps this signature.
 */
export function useTradeCandles(
  trade: Trade | null,
  interval: CandleInterval = '1h',
): UseTradeCandlesResult {
  const [candles, setCandles] = useState<Candle[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const request = useMemo(() => {
    if (!trade) return null

    const step = INTERVAL_SECONDS[interval]
    const opened = Math.floor(new Date(trade.openedAt).getTime() / 1000)
    const closed = trade.closedAt
      ? Math.floor(new Date(trade.closedAt).getTime() / 1000)
      : Math.floor(Date.now() / 1000)

    const tradeSpan = Math.max(closed - opened, step)
    const defaultSpan = DEFAULT_WINDOW_DAYS * 86_400
    // Pad both sides so the trade never touches the chart edges.
    const padding = Math.max(tradeSpan * 0.35, step * 6)
    const span = Math.max(defaultSpan, tradeSpan + padding * 2)

    const center = opened + tradeSpan / 2
    const from = Math.floor(center - span / 2)
    const to = Math.ceil(center + span / 2)

    return {
      symbol: trade.symbol,
      interval,
      from,
      to: Math.max(to, from + MIN_BARS * step),
    }
  }, [trade, interval])

  const anchors = useMemo(() => {
    if (!trade) return []
    const opened = Math.floor(new Date(trade.openedAt).getTime() / 1000)
    const points = [{ time: opened, price: trade.entry }]

    if (trade.closedAt && trade.exit != null) {
      points.push({
        time: Math.floor(new Date(trade.closedAt).getTime() / 1000),
        price: trade.exit,
      })
    } else if (trade.mark != null) {
      points.push({ time: Math.floor(Date.now() / 1000), price: trade.mark })
    }

    return points
  }, [trade])

  useEffect(() => {
    if (!request) {
      setCandles([])
      return
    }

    let cancelled = false
    setLoading(true)
    setError(null)

    const provider = createMockMarketData({ anchors })

    provider
      .getCandles(request)
      .then((data) => {
        if (!cancelled) setCandles(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load candles')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [request, anchors])

  return { candles, interval, loading, error }
}
