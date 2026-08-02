import { useEffect, useMemo, useState } from 'react'
import { ArrowDownRight, ArrowUpRight } from 'lucide-react'
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

const STABLES = new Set(['USDT', 'USDC', 'DAI', 'FDUSD', 'USDE', 'USDS'])
const REFRESH_MS = 60_000

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

function formatCompactUsd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `$${value.toLocaleString('en-US', {
    notation: 'compact',
    maximumFractionDigits: 2,
  })}`
}

function sparkPath(values: number[], width: number, height: number): string {
  if (values.length < 2) return ''
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  return values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * width
      const y = height - ((v - min) / span) * (height - 4) - 2
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
}

function sparkArea(values: number[], width: number, height: number): string {
  const line = sparkPath(values, width, height)
  if (!line) return ''
  return `${line} L${width},${height} L0,${height} Z`
}

function downsample(values: number[], maxPoints = 40): number[] {
  if (values.length <= maxPoints) return values
  const step = (values.length - 1) / (maxPoints - 1)
  const out: number[] = []
  for (let i = 0; i < maxPoints; i += 1) {
    out.push(values[Math.round(i * step)]!)
  }
  return out
}

async function fetchTopCoins(signal?: AbortSignal): Promise<TopCoin[]> {
  const response = await fetch(`${apiBase()}/api/v1/desk/top-coins?limit=10`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    cache: 'no-store',
    signal,
  })
  if (!response.ok) {
    throw new Error(`Top coins ${response.status}`)
  }
  const data = (await response.json()) as TopCoinsResponse
  return data.coins ?? []
}

function CoinTile({ coin }: { coin: TopCoin }) {
  const change = coin.change24hPct
  const up = (change ?? 0) > 0
  const down = (change ?? 0) < 0
  const stroke = down ? 'var(--color-short)' : up ? 'var(--color-long)' : 'var(--color-accent)'
  const points = useMemo(() => downsample(coin.sparkline ?? []), [coin.sparkline])
  const w = 160
  const h = 22
  const path = sparkPath(points, w, h)
  const area = sparkArea(points, w, h)
  const isStable = STABLES.has(coin.symbol)
  const arrowTone = up
    ? 'bg-[var(--color-long-soft)] text-[var(--color-long)]'
    : down
      ? 'bg-[var(--color-short-soft)] text-[var(--color-short)]'
      : 'bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]'
  const changeTone = up
    ? 'text-[var(--color-long)]'
    : down
      ? 'text-[var(--color-short)]'
      : 'text-[var(--color-text-muted)]'

  return (
    <article className="panel relative flex min-h-[108px] w-full flex-col items-center justify-center gap-2 px-4 pb-3.5 pt-4 text-center transition-colors hover:bg-[var(--color-surface-hover)]">
      <span className={`absolute right-3 top-3 rounded-lg p-1.5 ${arrowTone}`} aria-hidden>
        {up ? (
          <ArrowUpRight size={15} strokeWidth={1.8} />
        ) : down ? (
          <ArrowDownRight size={15} strokeWidth={1.8} />
        ) : (
          <ArrowUpRight size={15} strokeWidth={1.8} className="opacity-40" />
        )}
      </span>

      <div className="flex w-full items-center justify-center gap-1.5 px-6">
        {coin.imageUrl ? (
          <img
            src={coin.imageUrl}
            alt=""
            width={16}
            height={16}
            className="h-4 w-4 shrink-0 rounded-full"
            loading="lazy"
            referrerPolicy="no-referrer"
          />
        ) : null}
        <p className="truncate text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
          {coin.symbol}
        </p>
      </div>

      <p className="tabular w-full truncate px-2 text-xl font-semibold leading-none tracking-tight text-[var(--color-text)] sm:text-[1.35rem]">
        {formatUsdPrice(coin.priceUsd)}
      </p>

      <p className={`tabular text-xs font-medium ${changeTone}`}>
        {isStable
          ? `Circ. ${formatCompactUsd(coin.circulatingSupply)}`
          : change == null
            ? '—'
            : `${change > 0 ? '+' : ''}${change.toFixed(2)}%`}
      </p>

      {path ? (
        <svg
          width="100%"
          height={h}
          viewBox={`0 0 ${w} ${h}`}
          preserveAspectRatio="none"
          className="mt-0.5 block h-[22px] w-full max-w-[140px] opacity-90"
          aria-hidden
        >
          <path d={area} fill={stroke} opacity={0.14} />
          <path
            d={path}
            fill="none"
            stroke={stroke}
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      ) : null}
    </article>
  )
}

/** Top-10 market-cap banner — tile chrome matches KPI cards. */
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
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-muted)]">
          Top 10 Coins
        </h2>
        <span className="text-[10px] text-[var(--color-text-muted)]">Live · 7d</span>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {coins.map((coin) => (
          <CoinTile key={coin.id} coin={coin} />
        ))}
      </div>
    </section>
  )
}
