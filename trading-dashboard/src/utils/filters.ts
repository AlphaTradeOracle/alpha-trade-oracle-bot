import type { Trade, TradeFilterState } from '../types/trade'

function pnlValue(trade: Trade): number | null {
  if (trade.status === 'CLOSED') return trade.realized
  if (trade.status === 'OPEN') return trade.upnl
  return null
}

export function applyTradeFilters(trades: Trade[], filters: TradeFilterState): Trade[] {
  const q = filters.query.trim().toLowerCase()

  let rows = trades.filter((t) => {
    if (q && !t.symbol.toLowerCase().includes(q) && !t.id.includes(q)) return false
    if (filters.side !== 'all' && t.side !== filters.side) return false
    if (t.score < filters.minScore) return false

    const pnl = pnlValue(t)
    if (filters.pnl === 'profit' && (pnl == null || pnl <= 0)) return false
    if (filters.pnl === 'loss' && (pnl == null || pnl >= 0)) return false
    return true
  })

  const dir = filters.sortDir === 'asc' ? 1 : -1
  rows = [...rows].sort((a, b) => {
    const key = filters.sortBy
    const av = readSortValue(a, key)
    const bv = readSortValue(b, key)
    if (typeof av === 'string' && typeof bv === 'string') {
      return av.localeCompare(bv) * dir
    }
    return ((av as number) - (bv as number)) * dir
  })

  return rows
}

function readSortValue(trade: Trade, key: TradeFilterState['sortBy']): string | number {
  switch (key) {
    case 'symbol':
      return trade.symbol
    case 'score':
      return trade.score
    case 'upnl':
      return trade.upnl ?? Number.NEGATIVE_INFINITY
    case 'realized':
      return trade.realized ?? Number.NEGATIVE_INFINITY
    case 'r':
      return trade.r ?? Number.NEGATIVE_INFINITY
    case 'closedAt':
      return trade.closedAt ?? ''
    case 'openedAt':
    default:
      return trade.openedAt
  }
}

export const defaultFilters = (sortBy: TradeFilterState['sortBy'] = 'openedAt'): TradeFilterState => ({
  query: '',
  side: 'all',
  minScore: 0,
  pnl: 'all',
  sortBy,
  sortDir: 'desc',
})
