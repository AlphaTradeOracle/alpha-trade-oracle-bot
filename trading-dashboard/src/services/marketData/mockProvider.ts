import type { Candle } from '../../types/trade'
import {
  INTERVAL_SECONDS,
  type CandleRequest,
  type MarketDataProvider,
} from './types'

/**
 * Deterministic pseudo-random generator.
 * The same symbol/window always yields the same series, so the prototype does
 * not flicker between renders.
 */
function makeRandom(seed: number) {
  let state = seed >>> 0
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0
    return state / 0xffffffff
  }
}

function hashString(value: string): number {
  let h = 2166136261
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

/**
 * Anchor prices supplied by the caller keep the synthetic series close to the
 * trade it illustrates (entry / stop / exit stay inside the visible range).
 */
export interface MockAnchors {
  /** Price the series should gravitate towards at `anchorTime` */
  price: number
  /** Unix seconds of the anchor */
  time: number
}

export interface MockProviderOptions {
  anchors?: MockAnchors[]
  /** Relative candle body/wick size; 0.004 ≈ 0.4 % */
  volatility?: number
}

/**
 * Generates plausible OHLC data around the given anchors.
 * Replace with a real exchange adapter later — the interface stays identical.
 */
export function createMockMarketData(
  options: MockProviderOptions = {},
): MarketDataProvider {
  const { anchors = [], volatility = 0.006 } = options

  return {
    id: 'mock',
    async getCandles({ symbol, interval, from, to }: CandleRequest): Promise<Candle[]> {
      const step = INTERVAL_SECONDS[interval]
      const start = Math.floor(from / step) * step
      const end = Math.floor(to / step) * step
      const rand = makeRandom(hashString(`${symbol}:${interval}:${start}`))

      const sorted = [...anchors].sort((a, b) => a.time - b.time)
      const basePrice = sorted[0]?.price ?? 100

      /** Linear interpolation between anchors gives the series its drift. */
      const targetAt = (time: number): number => {
        if (sorted.length === 0) return basePrice
        if (time <= sorted[0].time) return sorted[0].price
        const last = sorted[sorted.length - 1]
        if (time >= last.time) return last.price
        for (let i = 1; i < sorted.length; i += 1) {
          const prev = sorted[i - 1]
          const next = sorted[i]
          if (time <= next.time) {
            const span = next.time - prev.time || 1
            const ratio = (time - prev.time) / span
            return prev.price + (next.price - prev.price) * ratio
          }
        }
        return last.price
      }

      const candles: Candle[] = []
      let close = targetAt(start)

      for (let t = start; t <= end; t += step) {
        const target = targetAt(t)
        const open = close
        // Pull towards the anchor path, then add symmetric noise.
        const drift = (target - open) * 0.35
        const noise = (rand() - 0.5) * open * volatility * 2
        close = Math.max(open + drift + noise, open * 0.5)

        const wick = open * volatility * (0.4 + rand() * 0.9)
        const high = Math.max(open, close) + wick * rand()
        const low = Math.min(open, close) - wick * rand()

        candles.push({
          time: t,
          open: Number(open.toFixed(8)),
          high: Number(high.toFixed(8)),
          low: Number(low.toFixed(8)),
          close: Number(close.toFixed(8)),
          volume: Number((1000 + rand() * 9000).toFixed(2)),
        })
      }

      return candles
    },
  }
}
