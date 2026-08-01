import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  HistoricalEquityProvider,
  createMockEquityData,
  type EquitySample,
} from '../services/equityData'
import { INTERVAL_SECONDS, type CandleInterval } from '../services/marketData'
import type { PortfolioSnapshot } from '../types/trade'

/** Points shown when the chart opens, before the user zooms out. */
const INITIAL_POINTS = 320
const PAGE_POINTS = 600

export interface EquitySeriesResult {
  samples: EquitySample[]
  loading: boolean
  loadingHistory: boolean
  exhausted: boolean
  error: string | null
  loadOlder: () => void
}

/**
 * Supplies the equity chart with samples for the selected timeframe.
 *
 * History is paged through `HistoricalEquityProvider`, so replacing the mock
 * source with a REST or WebSocket adapter needs no changes here or in the chart.
 */
export function useEquitySeries(
  portfolio: PortfolioSnapshot,
  interval: CandleInterval,
  enabled = true,
): EquitySeriesResult {
  const [samples, setSamples] = useState<EquitySample[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [exhausted, setExhausted] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const providerRef = useRef<HistoricalEquityProvider | null>(null)

  const source = useMemo(
    () =>
      createMockEquityData({
        startCapital: portfolio.totalCapital,
        currentEquity: portfolio.equity,
        openUpnl: portfolio.openUpnl,
      }),
    [portfolio.totalCapital, portfolio.equity, portfolio.openUpnl],
  )

  useEffect(() => {
    if (!enabled) {
      setSamples([])
      providerRef.current = null
      return
    }

    let cancelled = false
    const step = INTERVAL_SECONDS[interval]
    const provider = new HistoricalEquityProvider({
      interval,
      provider: source,
      pageSize: PAGE_POINTS,
    })
    providerRef.current = provider

    const now = Math.floor(Date.now() / 1000)

    setLoading(true)
    setError(null)
    setExhausted(false)

    provider
      .loadInitial(now - INITIAL_POINTS * step, now)
      .then((data) => {
        if (cancelled) return
        setSamples(data)
        setExhausted(provider.exhausted)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Equity konnte nicht geladen werden')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [source, interval, enabled])

  const loadOlder = useCallback(() => {
    const provider = providerRef.current
    if (!provider || provider.exhausted) return
    setLoadingHistory((busy) => {
      if (busy) return busy
      provider
        .loadOlder()
        .then((data) => {
          setSamples(data)
          setExhausted(provider.exhausted)
        })
        .catch(() => {
          /* keep the existing window on failure */
        })
        .finally(() => setLoadingHistory(false))
      return true
    })
  }, [])

  return { samples, loading, loadingHistory, exhausted, error, loadOlder }
}
