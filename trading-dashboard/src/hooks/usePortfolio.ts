import { useMemo } from 'react'
import portfolioJson from '../data/portfolio.json'
import equityJson from '../data/equity.json'
import type { EquityPoint, PortfolioSnapshot } from '../types/trade'

/**
 * Portfolio + equity curve loader.
 * Today: static JSON. Tomorrow: swap the import for fetch('/api/portfolio').
 */
export function usePortfolio() {
  const portfolio = useMemo(() => portfolioJson as PortfolioSnapshot, [])
  const equity = useMemo(() => equityJson as EquityPoint[], [])

  return {
    portfolio,
    equity,
    loading: false,
    error: null as string | null,
  }
}
