import { useEffect, useMemo, useState } from 'react'
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
/** CoinGecko sparkline is hourly over 7d (~168 pts); last 24 ≈ 1D. */
const SPARK_1D_POINTS = 24
const SPARK_W = 120
const SPARK_H = 52

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

/** Take the last ~24h from the hourly 7d sparkline. */
function sparkline1d(values: number[]): number[] {
  if (values.length <= SPARK_1D_POINTS) return values
  return values.slice(-SPARK_1D_POINTS)
}

function sparkPath(values: number[], width: number, height: number): string {
  if (values.length < 2) return ''
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pad = 2
  return values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * width
      const y = height - pad - ((v - min) / span) * (height - pad * 2)
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
}

function sparkArea(values: number[], width: number, height: number): string {
  const line = sparkPath(values, width, height)
  if (!line) return ''
  return `${line} L${width},${height} L0,${height} Z`
}

function CoinIcon({ coin }: { coin: TopCoin }) {
  const [failed, setFailed] = useState(false)
  const showImg = Boolean(coin.imageUrl) && !failed

  return (
    <span
      className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[var(--color-bg-elevated)] ring-1 ring-[var(--color-border-subtle)] sm:h-10 sm:w-10"
      aria-hidden
    >
      {showImg ? (
        <img
          src={coin.imageUrl!}
          alt=""
          width={40}
          height={40}
          className="h-full w-full object-cover"
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          {coin.symbol.slice(0, 2)}
        </span>
      )}
    </span>
  )
}

function Sparkline1d({ values, up, down }: { values: number[]; up: boolean; down: boolean }) {
  const points = useMemo(() => sparkline1d(values), [values])
  const path = sparkPath(points, SPARK_W, SPARK_H)
  const area = sparkArea(points, SPARK_W, SPARK_H)
  if (!path) return null

  const stroke = down
    ? 'var(--color-short)'
    : up
      ? 'var(--color-long)'
      : 'var(--color-accent)'

  return (
    <svg
      viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
      preserveAspectRatio="none"
      className="block h-[52px] w-[46%] max-w-[140px] min-w-[72px] shrink-0 opacity-95 sm:h-[58px] sm:max-w-[160px]"
      aria-hidden
    >
      <path d={area} fill={stroke} opacity={0.16} />
      <path
        d={path}
        fill="none"
        stroke={stroke}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
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
      : 'text-[var(--color-text-muted)]'
  const changeLabel =
    change == null ? '—' : `${change > 0 ? '+' : ''}${change.toFixed(2)}%`

  return (
    <div className="flex h-full min-h-[108px] items-center gap-2.5 px-3 py-3 sm:gap-3 sm:px-3.5">
      <div className="flex min-w-0 flex-1 flex-col items-start justify-center gap-1.5">
        <div className="flex items-center gap-2">
          <CoinIcon coin={coin} />
          <p className="text-xs font-semibold uppercase tracking-[0.1em] text-[var(--color-text-muted)] sm:text-[13px]">
            {coin.symbol}
          </p>
        </div>
        <p className="tabular w-full truncate text-lg font-semibold leading-none tracking-tight text-[var(--color-text)] sm:text-xl">
          {formatUsdPrice(coin.priceUsd)}
        </p>
        <p className={`tabular text-xs font-medium leading-none sm:text-[13px] ${changeTone}`}>
          {changeLabel}
        </p>
      </div>
      <Sparkline1d values={coin.sparkline ?? []} up={up} down={down} />
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
      <div className="panel flex min-h-[108px] items-center justify-center px-4">
        <p className="text-xs text-[var(--color-text-muted)]">Loading top markets…</p>
      </div>
    )
  }

  return (
    <section className="space-y-3">
      <h2 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-muted)]">
        Top 10 Coins
      </h2>
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
