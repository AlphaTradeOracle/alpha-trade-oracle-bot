import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import equityFallback from '../data/equity.json'
import portfolioFallback from '../data/portfolio.json'
import tradesFallback from '../data/trades.json'
import { fetchDeskSnapshot } from '../services/deskApi'
import type { EquityPoint, PortfolioSnapshot, Trade } from '../types/trade'

function isBookTrade(trade: Trade): boolean {
  if (trade.status !== 'CLOSED') return true
  return trade.exit != null
}

interface DeskDataValue {
  portfolio: PortfolioSnapshot
  trades: Trade[]
  equity: EquityPoint[]
  generatedAt: string | null
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
}

const DeskDataContext = createContext<DeskDataValue | null>(null)

export function DeskDataProvider({ children }: { children: ReactNode }) {
  const [portfolio, setPortfolio] = useState<PortfolioSnapshot>(
    () => portfolioFallback as PortfolioSnapshot,
  )
  const [trades, setTrades] = useState<Trade[]>(() =>
    (tradesFallback as Trade[]).filter(isBookTrade),
  )
  const [equity, setEquity] = useState<EquityPoint[]>(
    () => equityFallback as EquityPoint[],
  )
  const [generatedAt, setGeneratedAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const snap = await fetchDeskSnapshot()
      setPortfolio(snap.portfolio)
      setTrades((snap.trades ?? []).filter(isBookTrade))
      setEquity(snap.equity ?? [])
      setGeneratedAt(snap.generatedAt)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Desk-Daten konnten nicht geladen werden.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    fetchDeskSnapshot(controller.signal)
      .then((snap) => {
        setPortfolio(snap.portfolio)
        setTrades((snap.trades ?? []).filter(isBookTrade))
        setEquity(snap.equity ?? [])
        setGeneratedAt(snap.generatedAt)
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setError(err instanceof Error ? err.message : 'Desk-Daten konnten nicht geladen werden.')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  const value = useMemo(
    () => ({ portfolio, trades, equity, generatedAt, loading, error, refresh }),
    [portfolio, trades, equity, generatedAt, loading, error, refresh],
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
