import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  HistoricalDataProvider,
  INTERVAL_SECONDS,
  createMockMarketData,
  type CandleInterval,
} from '../services/marketData'
import type { PriceAnchor } from '../services/marketData'
import type { Candle, Trade } from '../types/trade'

/** Bars shown when the chart opens, before the user zooms out. */
const INITIAL_BARS = 260
const PAGE_BARS = 400

export interface TradeCandlesResult {
  candles: Candle[]
  loading: boolean
  /** True while an older page is being appended */
  loadingHistory: boolean
  /** No further history available from the source */
  exhausted: boolean
  error: string | null
  /** Ask for one more page of history (used when panning left) */
  loadOlder: () => void
}

/**
 * Supplies the trade chart with candles for the selected timeframe.
 *
 * History is paged through `HistoricalDataProvider`, so switching the mock
 * source for a REST or WebSocket adapter needs no changes here or in the chart.
 */
export function useTradeCandles(
  trade: Trade | null,
  interval: CandleInterval,
): TradeCandlesResult {
  const [candles, setCandles] = useState<Candle[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [exhausted, setExhausted] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const providerRef = useRef<HistoricalDataProvider | null>(null)

  const anchors = useMemo<PriceAnchor[]>(() => {
    if (!trade) return []
    const opened = Math.floor(new Date(trade.openedAt).getTime() / 1000)
    const points: PriceAnchor[] = [{ time: opened, price: trade.entry }]

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

  const tradeKey = trade ? `${trade.id}:${interval}` : null

  useEffect(() => {
    if (!trade || !tradeKey) {
      setCandles([])
      providerRef.current = null
      return
    }

    let cancelled = false
    const step = INTERVAL_SECONDS[interval]

    const source = createMockMarketData({ anchors })
    const provider = new HistoricalDataProvider({
      symbol: trade.symbol,
      interval,
      provider: source,
      pageSize: PAGE_BARS,
    })
    providerRef.current = provider

    // Window must cover the whole trade plus context on both sides.
    const opened = Math.floor(new Date(trade.openedAt).getTime() / 1000)
    const closed = trade.closedAt
      ? Math.floor(new Date(trade.closedAt).getTime() / 1000)
      : Math.floor(Date.now() / 1000)
    const span = Math.max(closed - opened, step)
    const padding = Math.max(span * 0.5, (INITIAL_BARS / 2) * step)

    setLoading(true)
    setError(null)
    setExhausted(false)

    provider
      .loadInitial(opened - padding, closed + padding)
      .then((data) => {
        if (cancelled) return
        setCandles(data)
        setExhausted(provider.exhausted)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Kerzen konnten nicht geladen werden')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [trade, tradeKey, interval, anchors])

  const loadOlder = useCallback(() => {
    const provider = providerRef.current
    if (!provider || provider.exhausted) return
    setLoadingHistory((busy) => {
      if (busy) return busy
      provider
        .loadOlder()
        .then((data) => {
          setCandles(data)
          setExhausted(provider.exhausted)
        })
        .catch(() => {
          /* keep the existing window on failure */
        })
        .finally(() => setLoadingHistory(false))
      return true
    })
  }, [])

  return { candles, loading, loadingHistory, exhausted, error, loadOlder }
}
