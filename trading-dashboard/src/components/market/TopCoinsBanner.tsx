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

const ACCENT: Record<string, string> = {
  BTC: '#f7931a',
  ETH: '#627eea',
  USDT: '#26a17b',
  BNB: '#f3ba2f',
  SOL: '#9945ff',
  XRP: '#23292f',
  USDC: '#2775ca',
  DOGE: '#c2a633',
  ADA: '#0033ad',
  TRX: '#ff0013',
  TON: '#0098ea',
  AVAX: '#e84142',
  LINK: '#2a5ada',
  DOT: '#e6007a',
  SHIB: '#ffa409',
}

function formatUsdPrice(price: number): string {
  if (price >= 1000) {
    return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  }
  if (price >= 1) {
    return `$${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  }
  if (price >= 0.01) {
    return `$${price.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })}`
  }
  return `$${price.toLocaleString('en-US', { minimumFractionDigits: 6, maximumFractionDigits: 6 })}`
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

function downsample(values: number[], maxPoints = 48): number[] {
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
  const accent = ACCENT[coin.symbol] ?? 'var(--color-accent)'
  const stroke = down ? 'var(--color-short)' : up ? 'var(--color-long)' : accent
  const points = useMemo(() => downsample(coin.sparkline ?? []), [coin.sparkline])
  const w = 88
  const h = 36
  const path = sparkPath(points, w, h)
  const area = sparkArea(points, w, h)
  const isStable = STABLES.has(coin.symbol)

  return (
    <article className="relative min-w-[168px] flex-1 overflow-hidden rounded-xl border border-[var(--color-border-subtle)] bg-[var(--color-surface)] px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {coin.imageUrl ? (
            <img
              src={coin.imageUrl}
              alt=""
              width={22}
              height={22}
              className="h-[22px] w-[22px] shrink-0 rounded-full"
              loading="lazy"
              referrerPolicy="no-referrer"
            />
          ) : (
            <span
              className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white"
              style={{ background: accent }}
            >
              {coin.symbol.slice(0, 1)}
            </span>
          )}
          <div className="min-w-0">
            <p className="truncate text-[12px] font-semibold leading-tight text-[var(--color-text)]">
              {coin.name}
            </p>
            <p className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
              {coin.symbol}
            </p>
          </div>
        </div>
        {up ? (
          <ArrowUpRight size={14} className="shrink-0 text-[var(--color-long)]" />
        ) : down ? (
          <ArrowDownRight size={14} className="shrink-0 text-[var(--color-short)]" />
        ) : null}
      </div>

      <div className="mt-2 flex items-end justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[15px] font-semibold tabular leading-none text-[var(--color-text)]">
            {formatUsdPrice(coin.priceUsd)}
          </p>
          {isStable ? (
            <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">
              Circ. {formatCompactUsd(coin.circulatingSupply)}
            </p>
          ) : (
            <p
              className={[
                'mt-1 text-[11px] font-medium tabular',
                up
                  ? 'text-[var(--color-long)]'
                  : down
                    ? 'text-[var(--color-short)]'
                    : 'text-[var(--color-text-muted)]',
              ].join(' ')}
            >
              {change == null
                ? '—'
                : `${change > 0 ? '+' : ''}${change.toFixed(2)}%`}
            </p>
          )}
        </div>
        {path ? (
          <svg
            width={w}
            height={h}
            viewBox={`0 0 ${w} ${h}`}
            className="shrink-0 opacity-90"
            aria-hidden
          >
            <path d={area} fill={stroke} opacity={0.16} />
            <path
              d={path}
              fill="none"
              stroke={stroke}
              strokeWidth={1.6}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        ) : null}
      </div>
    </article>
  )
}

/** Horizontal Top-10 market-cap banner with live CoinGecko prices. */
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
      <div className="overflow-hidden rounded-xl border border-[var(--color-border-subtle)] bg-[var(--color-surface)] px-4 py-3">
        <p className="text-xs text-[var(--color-text-muted)]">Loading top markets…</p>
      </div>
    )
  }

  return (
    <section className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-xs font-semibold uppercase tracking-[0.12em] text-[var(--color-text-muted)]">
          Top 10 Coins
        </h2>
        <span className="text-[10px] text-[var(--color-text-muted)]">Live · 7d sparkline</span>
      </div>
      <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:thin]">
        {coins.map((coin) => (
          <CoinTile key={coin.id} coin={coin} />
        ))}
      </div>
    </section>
  )
}
