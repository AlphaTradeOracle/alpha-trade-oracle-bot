import { useMemo } from 'react'
import tradesJson from '../data/trades.json'
import type { Trade, TradeStatus } from '../types/trade'

/** Closed book rows must have an exit fill — cancelled/retest skips never qualify. */
function isBookTrade(trade: Trade): boolean {
  if (trade.status !== 'CLOSED') return true
  return trade.exit != null
}

/**
 * Trade book loader.
 * Swap `tradesJson` for an API client later (Binance/Bybit/Hyperliquid adapters).
 */
export function useTrades(status?: TradeStatus) {
  const all = useMemo(
    () => (tradesJson as Trade[]).filter(isBookTrade),
    [],
  )

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
