import type { Candle } from '../../types/trade'
import { createPriceCurve, type PriceAnchor } from './priceSeries'
import {
  INTERVAL_SECONDS,
  alignToInterval,
  type CandleRequest,
  type MarketDataProvider,
} from './types'

export interface MockProviderOptions {
  /** Known prices the curve must pass through (entry, exit, mark …) */
  anchors?: PriceAnchor[]
  /** How far back the synthetic history reaches (unix seconds) */
  earliestTime?: number
  /** Simulated network latency in ms — keeps loading states honest */
  latencyMs?: number
}

/** Legacy alias kept so existing imports stay valid. */
export type MockAnchors = PriceAnchor

const DEFAULT_HISTORY_SECONDS = 86_400 * 400

/**
 * Synthetic candle source.
 *
 * Values derive from a continuous price curve, so any window — including ones
 * requested later while scrolling back — stitches together without seams.
 * Swap this for a REST or WebSocket adapter; the interface stays the same.
 */
export function createMockMarketData(
  options: MockProviderOptions = {},
): MarketDataProvider {
  const { anchors = [], latencyMs = 0 } = options
  const earliestTime =
    options.earliestTime ??
    Math.floor(Date.now() / 1000) - DEFAULT_HISTORY_SECONDS

  return {
    id: 'mock',
    earliestTime,

    async getCandles({ symbol, interval, from, to }: CandleRequest): Promise<Candle[]> {
      if (latencyMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, latencyMs))
      }

      const step = INTERVAL_SECONDS[interval]
      const start = Math.max(alignToInterval(from, interval), alignToInterval(earliestTime, interval))
      const end = alignToInterval(to, interval)
      if (end < start) return []

      const curve = createPriceCurve({ symbol, anchors })
      const candles: Candle[] = []

      for (let t = start; t <= end; t += step) {
        const open = curve.at(t)
        const close = curve.at(t + step)

        // Sample inside the bar so wicks reflect the same underlying curve.
        const mid1 = curve.at(t + step * 0.33)
        const mid2 = curve.at(t + step * 0.66)

        const body = [open, close, mid1, mid2]
        const wickScale = 0.0015 + curve.jitter(t, 1) * 0.004
        const high = Math.max(...body) * (1 + wickScale * curve.jitter(t, 2))
        const low = Math.min(...body) * (1 - wickScale * curve.jitter(t, 3))

        candles.push({
          time: t,
          open: round(open),
          high: round(high),
          low: round(low),
          close: round(close),
          volume: Number((500 + curve.jitter(t, 4) * 9500).toFixed(2)),
        })
      }

      return candles
    },
  }
}

function round(value: number): number {
  return Number(value.toPrecision(10))
}
