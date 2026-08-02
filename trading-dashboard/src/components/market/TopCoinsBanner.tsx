import { useEffect, useState } from 'react'
import { apiBase } from '../../services/deskApi'

export interface TopCoin {
  id: string
  symbol: string
  name: string
  rank: number
  priceUsd: number
  change24hPct: number | null
  marketCapUsd: number | null
  volume24hUsd: number | null
  circulatingSupply: number | null
  imageUrl: string | null
  sparkline: number[]
}

interface TopCoinsResponse {
  coins: TopCoin[]
  generatedAt: string
  source: string
}

/** Client-side safety net; API already drops these. */
const STABLES = new Set(['USDT', 'USDC', 'DAI', 'FDUSD', 'USDE', 'USDS', 'TUSD', 'USDD'])
const REFRESH_MS = 60_000
const DISPLAY_COUNT = 10
/** Fetch a few extra so USDT/USDC (and other stables) can be skipped. */
const FETCH_LIMIT = 15

function formatUsdPrice(price: number): string {
  if (price >= 1000) {
    return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  }
  if (price >= 1) {
    return `$${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  }
  if (price >= 0.1) {
    return `$${price.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })}`
  }
  if (price >= 0.01) {
    return `$${price.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })}`
  }
  return `$${price.toLocaleString('en-US', { minimumFractionDigits: 5, maximumFractionDigits: 6 })}`
}

async function fetchTopCoins(signal?: AbortSignal): Promise<TopCoin[]> {
  const response = await fetch(`${apiBase()}/api/v1/desk/top-coins?limit=${FETCH_LIMIT}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    cache: 'no-store',
    signal,
  })
  if (!response.ok) {
    throw new Error(`Top coins ${response.status}`)
  }
  const data = (await response.json()) as TopCoinsResponse
  return (data.coins ?? [])
    .filter((c) => !STABLES.has(c.symbol.toUpperCase()))
    .slice(0, DISPLAY_COUNT)
}

function CoinChip({ coin }: { coin: TopCoin }) {
  const change = coin.change24hPct
  const up = (change ?? 0) > 0
  const down = (change ?? 0) < 0
  const changeTone = up
    ? 'text-[var(--color-long)]'
    : down
      ? 'text-[var(--color-short)]'
      : 'text-[var(--color-text-muted)]'
  const changeLabel =
    change == null ? '—' : `${change > 0 ? '+' : ''}${change.toFixed(2)}%`

  return (
    <div className="flex min-h-[72px] flex-col items-center justify-center gap-1 px-2 py-2.5 text-center">
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-muted)]">
        {coin.symbol}
      </p>
      <p className="tabular truncate text-sm font-semibold leading-none tracking-tight text-[var(--color-text)] sm:text-[0.95rem]">
        {formatUsdPrice(coin.priceUsd)}
      </p>
      <p className={`tabular text-[11px] font-medium leading-none ${changeTone}`}>{changeLabel}</p>
    </div>
  )
}

/** Compact top-10 majors banner (no stables) — full-width strip aligned with KPI grid. */
export function TopCoinsBanner() {
  const [coins, setCoins] = useState<TopCoin[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let alive = true

    const load = async () => {
      try {
        const next = await fetchTopCoins(controller.signal)
        if (!alive) return
        setCoins(next)
        setError(null)
      } catch (err) {
        if (!alive || controller.signal.aborted) return
        setError(err instanceof Error ? err.message : 'Top coins failed')
      }
    }

    void load()
    const timer = window.setInterval(() => void load(), REFRESH_MS)
    return () => {
      alive = false
      controller.abort()
      window.clearInterval(timer)
    }
  }, [])

  if (error && coins.length === 0) {
    return null
  }
  if (coins.length === 0) {
    return (
      <div className="panel flex min-h-[72px] items-center justify-center px-4">
        <p className="text-xs text-[var(--color-text-muted)]">Loading top markets…</p>
      </div>
    )
  }

  return (
    <section className="space-y-3">
      <h2 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-muted)]">
        Top 10 Coins
      </h2>
      {/* Same gap / column rhythm as KpiGrid (2→3→4→5/6); 5×2 fills 10 majors. */}
      <div className="panel grid grid-cols-2 gap-px overflow-hidden bg-[var(--color-border)] sm:grid-cols-3 md:grid-cols-5 xl:grid-cols-5 2xl:grid-cols-5">
        {coins.map((coin) => (
          <div
            key={coin.id}
            className="bg-[var(--color-surface)] transition-colors hover:bg-[var(--color-surface-hover)]"
          >
            <CoinChip coin={coin} />
          </div>
        ))}
      </div>
    </section>
  )
}
