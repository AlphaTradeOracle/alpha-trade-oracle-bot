import type { EquityPoint } from '../../types/trade'
import { INTERVAL_SECONDS, alignToInterval, type CandleInterval } from '../marketData'
import type { EquityDataProvider, EquitySample, EquitySeriesRequest } from './types'

function toUnix(t: string): number {
  return Math.floor(new Date(t).getTime() / 1000)
}

function withDrawdown(points: Array<{ time: number; equity: number }>): EquitySample[] {
  let peak = Number.NEGATIVE_INFINITY
  return points.map((point) => {
    peak = Math.max(peak, point.equity)
    const drawdownPct = peak > 0 ? ((point.equity - peak) / peak) * 100 : 0
    return {
      time: point.time,
      equity: point.equity,
      balance: point.equity,
      drawdownPct,
    }
  })
}

/**
 * Resample fill-level desk equity points onto the chart interval grid.
 * Forward-fills between fills so ranges with sparse activity still plot.
 */
export function createDeskEquityData(points: EquityPoint[]): EquityDataProvider {
  const raw = [...points]
    .map((p) => ({ time: toUnix(p.t), equity: p.equity }))
    .filter((p) => Number.isFinite(p.time) && Number.isFinite(p.equity))
    .sort((a, b) => a.time - b.time)

  const earliestTime = raw[0]?.time

  return {
    id: 'desk',
    earliestTime,
    async getSeries({ interval, from, to }: EquitySeriesRequest): Promise<EquitySample[]> {
      if (raw.length === 0) return []

      const step = INTERVAL_SECONDS[interval as CandleInterval] ?? INTERVAL_SECONDS['1h']
      const start = alignToInterval(Math.max(from, raw[0].time), interval)
      const end = alignToInterval(Math.min(to, raw[raw.length - 1].time), interval)
      if (end < start) return []

      const sampled: Array<{ time: number; equity: number }> = []
      let cursor = 0
      let lastEquity = raw[0].equity

      for (let t = start; t <= end; t += step) {
        while (cursor < raw.length && raw[cursor].time <= t) {
          lastEquity = raw[cursor].equity
          cursor += 1
        }
        sampled.push({ time: t, equity: lastEquity })
      }

      // Always keep exact fill timestamps inside the window for sharp steps.
      for (const point of raw) {
        if (point.time < from || point.time > to) continue
        sampled.push(point)
      }

      const byTime = new Map<number, number>()
      for (const point of sampled.sort((a, b) => a.time - b.time)) {
        byTime.set(point.time, point.equity)
      }
      return withDrawdown(
        [...byTime.entries()].map(([time, equity]) => ({ time, equity })),
      )
    },
  }
}
