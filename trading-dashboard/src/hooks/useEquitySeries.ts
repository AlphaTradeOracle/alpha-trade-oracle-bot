import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getRange, type EquityRangeId } from '../components/equity/EquityFilters'
import {
  HistoricalEquityProvider,
  createDeskEquityData,
  type EquitySample,
} from '../services/equityData'
import type { EquityPoint, PortfolioSnapshot } from '../types/trade'

const PAGE_POINTS = 600

/** Fallback lookback for the "since account opening" range. */
const MAX_HISTORY_SECONDS = 540 * 86_400

export interface EquitySeriesResult {
  samples: EquitySample[]
  loading: boolean
  loadingHistory: boolean
  exhausted: boolean
  error: string | null
  loadOlder: () => void
}

/**
 * Supplies the equity chart from live desk fill-curve points.
 */
export function useEquitySeries(
  portfolio: PortfolioSnapshot,
  equityPoints: EquityPoint[],
  rangeId: EquityRangeId,
): EquitySeriesResult {
  const [samples, setSamples] = useState<EquitySample[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [exhausted, setExhausted] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const providerRef = useRef<HistoricalEquityProvider | null>(null)

  const source = useMemo(() => {
    const points =
      equityPoints.length > 0
        ? equityPoints
        : [
            { t: new Date().toISOString(), equity: portfolio.equity },
          ]
    return createDeskEquityData(points)
  }, [equityPoints, portfolio.equity])

  const range = getRange(rangeId)

  useEffect(() => {
    let cancelled = false
    const provider = new HistoricalEquityProvider({
      interval: range.interval,
      provider: source,
      pageSize: PAGE_POINTS,
    })
    providerRef.current = provider

    const now = Math.floor(Date.now() / 1000)
    const lookback = range.seconds ?? MAX_HISTORY_SECONDS
    const from = Math.max(now - lookback, source.earliestTime ?? now - lookback)

    setLoading(true)
    setError(null)
    setExhausted(false)

    provider
      .loadInitial(from, now)
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
  }, [source, range.interval, range.seconds])

  const loadOlder = useCallback(() => {
    const provider = providerRef.current
    if (!provider || provider.exhausted || range.seconds != null) return
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
  }, [range.seconds])

  return { samples, loading, loadingHistory, exhausted, error, loadOlder }
}
