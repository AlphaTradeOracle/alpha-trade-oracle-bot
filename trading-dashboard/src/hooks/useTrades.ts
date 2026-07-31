import { useMemo } from 'react'
import tradesJson from '../data/trades.json'
import type { Trade, TradeStatus } from '../types/trade'

/**
 * Trade book loader.
 * Swap `tradesJson` for an API client later (Binance/Bybit/Hyperliquid adapters).
 */
export function useTrades(status?: TradeStatus) {
  const all = useMemo(() => tradesJson as Trade[], [])

  const trades = useMemo(() => {
    if (!status) return all
    return all.filter((t) => t.status === status)
  }, [all, status])

  return {
    trades,
    all,
    loading: false,
    error: null as string | null,
  }
}
