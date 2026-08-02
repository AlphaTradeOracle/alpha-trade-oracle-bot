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
import equityFallback from '../data/equity.json'
import portfolioFallback from '../data/portfolio.json'
import tradesFallback from '../data/trades.json'
import { fetchDeskSnapshot } from '../services/deskApi'
import type {
  EquityPoint,
  MarketRegimeSnapshot,
  PortfolioSnapshot,
  Trade,
} from '../types/trade'

/** While the desk tab stays open, refresh in the background. */
const POLL_MS = 60_000

function isBookTrade(trade: Trade): boolean {
  if (trade.status !== 'CLOSED') return true
  return trade.exit != null
}

/**
 * Static JSON is only an offline/demo seed. Never show OPEN/PENDING from it —
 * those go stale (e.g. cancelled KAVA) and flash on every hard refresh until
 * `/desk/snapshot` replaces the book.
 */
function seedTrades(): Trade[] {
  return (tradesFallback as Trade[])
    .filter((t) => t.status === 'CLOSED')
    .filter(isBookTrade)
}

function seedPortfolio(): PortfolioSnapshot {
  const base = portfolioFallback as PortfolioSnapshot
  return {
    ...base,
    openPositions: 0,
    pendingOrders: 0,
    openUpnl: 0,
    openR: 0,
    marginLocked: 0,
  }
}

interface RefreshOptions {
  /** Skip the full-page loading flag (background poll). */
  silent?: boolean
}

interface DeskDataValue {
  portfolio: PortfolioSnapshot
  trades: Trade[]
  equity: EquityPoint[]
  marketRegime: MarketRegimeSnapshot | null
  generatedAt: string | null
  loading: boolean
  error: string | null
  refresh: (options?: RefreshOptions) => Promise<void>
}

const DeskDataContext = createContext<DeskDataValue | null>(null)

export function DeskDataProvider({ children }: { children: ReactNode }) {
  const [portfolio, setPortfolio] = useState<PortfolioSnapshot>(seedPortfolio)
  const [trades, setTrades] = useState<Trade[]>(seedTrades)
  const [equity, setEquity] = useState<EquityPoint[]>(
    () => equityFallback as EquityPoint[],
  )
  const [marketRegime, setMarketRegime] = useState<MarketRegimeSnapshot | null>(null)
  const [generatedAt, setGeneratedAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  /** Monotonic id so only the latest request may clear loading / write state. */
  const requestId = useRef(0)
  const inFlight = useRef<AbortController | null>(null)
  /** True while a non-silent (button / first load) refresh owns the spinner. */
  const manualInFlight = useRef(false)

  const refresh = useCallback(async (options?: RefreshOptions) => {
    const silent = Boolean(options?.silent)

    // Background polls must not cancel / steal a visible refresh.
    if (silent && manualInFlight.current) {
      return
    }

    inFlight.current?.abort()
    const controller = new AbortController()
    inFlight.current = controller
    const id = ++requestId.current

    if (!silent) {
      manualInFlight.current = true
      setLoading(true)
      setError(null)
    }

    try {
      const snap = await fetchDeskSnapshot(controller.signal)
      if (id !== requestId.current) return
      setPortfolio(snap.portfolio)
      setTrades((snap.trades ?? []).filter(isBookTrade))
      setEquity(snap.equity ?? [])
      setMarketRegime(snap.marketRegime ?? null)
      setGeneratedAt(snap.generatedAt)
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
          setLoading(false)
        } else if (!manualInFlight.current) {
          // Keep spinner honest if a prior aborted manual left it up.
          setLoading(false)
        }
      }
    }
  }, [])

  // One initial load for the whole desk shell (shared across routes).
  useEffect(() => {
    void refresh()
    return () => {
      requestId.current += 1
      inFlight.current?.abort()
      inFlight.current = null
      manualInFlight.current = false
    }
  }, [refresh])

  // Background poll only while the browser tab is visible.
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
      error,
      refresh,
    }),
    [portfolio, trades, equity, marketRegime, generatedAt, loading, error, refresh],
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
