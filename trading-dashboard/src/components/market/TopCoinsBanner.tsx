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
const FETCH_LIMIT = 15

/** Compact ticker row — room for readable type without towering the page. */
const BANNER_H = 'h-[3.25rem]'

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

function CoinIcon({ coin }: { coin: TopCoin }) {
  const [failed, setFailed] = useState(false)
  const showImg = Boolean(coin.imageUrl) && !failed

  return (
    <span
      className="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[var(--color-bg)] ring-1 ring-[var(--color-border)]"
      aria-hidden
    >
      {showImg ? (
        <img
          src={coin.imageUrl!}
          alt=""
          width={28}
          height={28}
          className="h-full w-full object-cover"
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="text-[10px] font-semibold uppercase text-[var(--color-text-secondary)]">
          {coin.symbol.slice(0, 2)}
        </span>
      )}
    </span>
  )
}

function CoinChip({ coin }: { coin: TopCoin }) {
  const change = coin.change24hPct
  const up = (change ?? 0) > 0
  const down = (change ?? 0) < 0
  const changeTone = up
    ? 'text-[var(--color-long)]'
    : down
      ? 'text-[var(--color-short)]'
      : 'text-[var(--color-text-secondary)]'
  const changeLabel =
    change == null ? '—' : `${change > 0 ? '+' : ''}${change.toFixed(2)}%`

  return (
    <div
      className={`flex ${BANNER_H} min-w-0 flex-1 items-center gap-2 px-1.5 sm:px-2`}
      title={`${coin.name} · ${formatUsdPrice(coin.priceUsd)} · ${changeLabel} (24h)`}
    >
      <CoinIcon coin={coin} />
      <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5 leading-none">
        <div className="flex min-w-0 items-baseline gap-1.5">
          <span className="shrink-0 text-xs font-semibold uppercase tracking-[0.04em] text-[var(--color-text)]">
            {coin.symbol}
          </span>
          <span className={`tabular shrink-0 text-xs font-semibold ${changeTone}`}>
            {changeLabel}
          </span>
        </div>
        <p className="tabular truncate text-[13px] font-semibold tracking-tight text-[var(--color-text)]">
          {formatUsdPrice(coin.priceUsd)}
        </p>
      </div>
    </div>
  )
}

/** Slim top banner — one row of 10 majors, height matches Sidebar nav items. */
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
      <div className={`flex ${BANNER_H} items-center justify-center bg-transparent px-3`}>
        <p className="text-[10px] text-[var(--color-text-muted)]">Loading markets…</p>
      </div>
    )
  }

  return (
    <section
      aria-label="Top 10 coins"
      className={`flex ${BANNER_H} w-full items-stretch overflow-x-auto overflow-y-hidden bg-transparent`}
    >
      {coins.map((coin) => (
        <div key={coin.id} className="flex min-w-[7.25rem] flex-1 basis-0 items-stretch">
          <CoinChip coin={coin} />
        </div>
      ))}
    </section>
  )
}
