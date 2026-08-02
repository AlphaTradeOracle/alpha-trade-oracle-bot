import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { fetchDeskSnapshot } from '../services/deskApi'
import type {
  EquityPoint,
  MarketRegimeSnapshot,
  PortfolioSnapshot,
  Trade,
} from '../types/trade'

/** While the desk tab stays open, refresh in the background. */
const POLL_MS = 60_000

/** Neutral book until the first live `/desk/snapshot` lands — never flash demo JSON. */
function emptyPortfolio(): PortfolioSnapshot {
  return {
    totalCapital: 0,
    equity: 0,
    cash: 0,
    marginLocked: 0,
    realizedPnl: 0,
    openUpnl: 0,
    openR: 0,
    totalReturnPct: 0,
    openPositions: 0,
    pendingOrders: 0,
    closedTrades: 0,
  }
}

function isBookTrade(trade: Trade): boolean {
  if (trade.status !== 'CLOSED') return true
  return trade.exit != null
}

interface RefreshOptions {
  /** Skip the button spinner (background poll). */
  silent?: boolean
}

interface DeskDataValue {
  portfolio: PortfolioSnapshot
  trades: Trade[]
  equity: EquityPoint[]
  marketRegime: MarketRegimeSnapshot | null
  generatedAt: string | null
  /** True only before the first successful live snapshot. */
  loading: boolean
  /** True while a user-triggered / initial fetch is in flight (soft UI). */
  refreshing: boolean
  error: string | null
  refresh: (options?: RefreshOptions) => Promise<void>
}

const DeskDataContext = createContext<DeskDataValue | null>(null)

export function DeskDataProvider({ children }: { children: ReactNode }) {
  const [portfolio, setPortfolio] = useState<PortfolioSnapshot>(emptyPortfolio)
  const [trades, setTrades] = useState<Trade[]>([])
  const [equity, setEquity] = useState<EquityPoint[]>([])
  const [marketRegime, setMarketRegime] = useState<MarketRegimeSnapshot | null>(null)
  const [generatedAt, setGeneratedAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const requestId = useRef(0)
  const inFlight = useRef<AbortController | null>(null)
  const manualInFlight = useRef(false)
  const hasLiveRef = useRef(false)

  const refresh = useCallback(async (options?: RefreshOptions) => {
    const silent = Boolean(options?.silent)

    if (silent && manualInFlight.current) {
      return
    }

    inFlight.current?.abort()
    const controller = new AbortController()
    inFlight.current = controller
    const id = ++requestId.current

    if (!silent) {
      manualInFlight.current = true
      setRefreshing(true)
      setError(null)
      // Hard loading gate only until the first live snapshot lands.
      if (!hasLiveRef.current) {
        setLoading(true)
      }
    }

    try {
      const snap = await fetchDeskSnapshot(controller.signal)
      if (id !== requestId.current) return
      setPortfolio(snap.portfolio)
      setTrades((snap.trades ?? []).filter(isBookTrade))
      setEquity(snap.equity ?? [])
      setMarketRegime(snap.marketRegime ?? null)
      setGeneratedAt(snap.generatedAt)
      hasLiveRef.current = true
      setError(null)
    } catch (err) {
      if (id !== requestId.current) return
      if (controller.signal.aborted) return
      setError(err instanceof Error ? err.message : 'Desk-Daten konnten nicht geladen werden.')
    } finally {
      if (id === requestId.current) {
        inFlight.current = null
        if (!silent) {
          manualInFlight.current = false
          setRefreshing(false)
          setLoading(false)
        } else if (!manualInFlight.current) {
          setLoading(false)
          setRefreshing(false)
        }
      }
    }
  }, [])

  useEffect(() => {
    void refresh()
    return () => {
      requestId.current += 1
      inFlight.current?.abort()
      inFlight.current = null
      manualInFlight.current = false
    }
  }, [refresh])

  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === 'visible') {
        void refresh({ silent: true })
      }
    }
    const id = window.setInterval(tick, POLL_MS)
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void refresh({ silent: true })
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [refresh])

  const value = useMemo(
    () => ({
      portfolio,
      trades,
      equity,
      marketRegime,
      generatedAt,
      loading,
      refreshing,
      error,
      refresh,
    }),
    [
      portfolio,
      trades,
      equity,
      marketRegime,
      generatedAt,
      loading,
      refreshing,
      error,
      refresh,
    ],
  )

  return <DeskDataContext.Provider value={value}>{children}</DeskDataContext.Provider>
}

export function useDeskData(): DeskDataValue {
  const ctx = useContext(DeskDataContext)
  if (!ctx) {
    throw new Error('useDeskData must be used within DeskDataProvider')
  }
  return ctx
}
