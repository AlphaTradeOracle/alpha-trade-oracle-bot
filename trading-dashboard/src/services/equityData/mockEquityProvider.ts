import { INTERVAL_SECONDS, alignToInterval, createPriceCurve } from '../marketData'
import type { EquityDataProvider, EquitySample, EquitySeriesRequest } from './types'

export interface MockEquityOptions {
  /** Capital the account started with */
  startCapital: number
  /** Current mark-to-market equity */
  currentEquity: number
  /** Unrealised PnL of open positions, used to derive balance */
  openUpnl?: number
  /** How far back the synthetic history reaches, in days */
  historyDays?: number
}

const DEFAULT_HISTORY_DAYS = 540

/**
 * Synthetic equity source.
 *
 * Values come from a continuous curve anchored at the account's start capital
 * and its current equity, so any requested window — including older pages
 * fetched while scrolling back — lines up seamlessly.
 */
export function createMockEquityData({
  startCapital,
  currentEquity,
  openUpnl = 0,
  historyDays = DEFAULT_HISTORY_DAYS,
}: MockEquityOptions): EquityDataProvider {
  const now = Math.floor(Date.now() / 1000)
  const earliestTime = now - historyDays * 86_400

  // A single curve backs every timeframe, which keeps the shape consistent
  // when the user switches between them.
  const curve = createPriceCurve({
    symbol: 'ACCOUNT_EQUITY',
    amplitude: 0.16,
    anchors: [
      { time: earliestTime, price: startCapital * 0.62 },
      { time: now - 180 * 86_400, price: startCapital * 0.88 },
      { time: now, price: currentEquity },
    ],
  })

  return {
    id: 'mock',
    earliestTime,

    async getSeries({ interval, from, to }: EquitySeriesRequest): Promise<EquitySample[]> {
      const step = INTERVAL_SECONDS[interval]
      const start = Math.max(alignToInterval(from, interval), alignToInterval(earliestTime, interval))
      const end = Math.min(alignToInterval(to, interval), alignToInterval(now, interval))
      if (end < start) return []

      // Running peak has to start from the true high before `start`, otherwise
      // drawdown would reset every time an older page is loaded.
      let peak = highWaterMark(curve.at, earliestTime, start, step)
      const samples: EquitySample[] = []

      for (let t = start; t <= end; t += step) {
        const equity = curve.at(t)
        peak = Math.max(peak, equity)
        samples.push({
          time: t,
          equity: round(equity),
          balance: round(equity - openUpnl * fadeIn(t, now)),
          drawdownPct: round(((equity - peak) / peak) * 100),
        })
      }

      return samples
    },
  }
}

/** Coarse scan of the pre-window high so drawdowns stay stable across pages. */
function highWaterMark(
  at: (time: number) => number,
  from: number,
  to: number,
  step: number,
): number {
  const scanStep = Math.max(step, 86_400)
  let peak = 0
  for (let t = from; t < to; t += scanStep) {
    peak = Math.max(peak, at(t))
  }
  return peak || at(from)
}

/** Open PnL only exists near the present; older balance equals equity. */
function fadeIn(time: number, now: number): number {
  const window = 7 * 86_400
  if (time >= now) return 1
  if (time <= now - window) return 0
  return (time - (now - window)) / window
}

function round(value: number): number {
  return Number(value.toFixed(2))
}
