import type { MarketRegimeSnapshot } from '../types/market'
import type { EquityPoint, PortfolioSnapshot, Trade } from '../types/trade'

export interface DeskSnapshot {
  portfolio: PortfolioSnapshot
  trades: Trade[]
  equity: EquityPoint[]
  generatedAt: string
  marketRegime?: MarketRegimeSnapshot | null
}

/** Same-origin in production (Nginx proxies `/api`). Override for local Vite. */
export function apiBase(): string {
  const raw = import.meta.env.VITE_DESK_API_BASE
  if (typeof raw === 'string' && raw.length > 0) {
    return raw.replace(/\/$/, '')
  }
  return ''
}

export async function fetchDeskSnapshot(signal?: AbortSignal): Promise<DeskSnapshot> {
  const response = await fetch(`${apiBase()}/api/v1/desk/snapshot`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    cache: 'no-store',
    signal,
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(
      detail
        ? `Desk API ${response.status}: ${detail.slice(0, 200)}`
        : `Desk API ${response.status}`,
    )
  }
  return (await response.json()) as DeskSnapshot
}
