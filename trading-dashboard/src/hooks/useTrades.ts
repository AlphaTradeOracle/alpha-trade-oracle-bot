import { useMemo } from 'react'
import { useDeskData } from '../context/DeskDataContext'
import type { TradeStatus } from '../types/trade'

/** Live trade book from `/api/v1/desk/snapshot` (fallback JSON until first load). */
export function useTrades(status?: TradeStatus) {
  const { trades: all, loading, error } = useDeskData()

  const trades = useMemo(() => {
    if (!status) return all
    return all.filter((t) => t.status === status)
  }, [all, status])

  return {
    trades,
    all,
    loading,
    error,
  }
}
