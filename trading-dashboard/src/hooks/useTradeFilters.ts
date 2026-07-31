import { useMemo, useState } from 'react'
import type { Trade, TradeFilterState } from '../types/trade'
import { applyTradeFilters, defaultFilters } from '../utils/filters'

export function useTradeFilters(
  trades: Trade[],
  initialSort: TradeFilterState['sortBy'] = 'openedAt',
) {
  const [filters, setFilters] = useState<TradeFilterState>(() => defaultFilters(initialSort))

  const filtered = useMemo(() => applyTradeFilters(trades, filters), [trades, filters])

  return { filters, setFilters, filtered }
}
