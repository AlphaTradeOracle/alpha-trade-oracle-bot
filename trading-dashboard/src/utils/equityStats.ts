import type { EquitySample } from '../services/equityData'
import type { Trade } from '../types/trade'

export interface EquityStats {
  current: number
  high: number
  low: number
  totalReturnPct: number
  cagrPct: number | null
  maxDrawdownPct: number
  profitFactor: number | null
  winratePct: number | null
  averageR: number | null
  averageDailyPnl: number | null
  averageMonthlyPnl: number | null
}

/**
 * Derives headline metrics from the loaded equity window and the closed book.
 * Pure and cheap so it can run on every render of the analysis modal.
 */
export function computeEquityStats(samples: EquitySample[], closed: Trade[]): EquityStats {
  if (samples.length === 0) {
    return {
      current: 0,
      high: 0,
      low: 0,
      totalReturnPct: 0,
      cagrPct: null,
      maxDrawdownPct: 0,
      profitFactor: null,
      winratePct: null,
      averageR: null,
      averageDailyPnl: null,
      averageMonthlyPnl: null,
    }
  }

  const first = samples[0]
  const last = samples[samples.length - 1]

  let high = first.equity
  let low = first.equity
  let peak = first.equity
  let maxDrawdownPct = 0

  for (const s of samples) {
    if (s.equity > high) high = s.equity
    if (s.equity < low) low = s.equity
    peak = Math.max(peak, s.equity)
    const dd = ((s.equity - peak) / peak) * 100
    if (dd < maxDrawdownPct) maxDrawdownPct = dd
  }

  const totalReturnPct = first.equity > 0 ? ((last.equity - first.equity) / first.equity) * 100 : 0

  const years = (last.time - first.time) / (365.25 * 86_400)
  const cagrPct =
    years > 0.05 && first.equity > 0
      ? ((last.equity / first.equity) ** (1 / years) - 1) * 100
      : null

  const days = Math.max((last.time - first.time) / 86_400, 1)
  const pnl = last.equity - first.equity
  const averageDailyPnl = pnl / days
  const averageMonthlyPnl = averageDailyPnl * 30.44

  return {
    current: last.equity,
    high,
    low,
    totalReturnPct,
    cagrPct,
    maxDrawdownPct,
    averageDailyPnl,
    averageMonthlyPnl,
    ...computeTradeStats(closed),
  }
}

function computeTradeStats(closed: Trade[]) {
  const settled = closed.filter((t) => t.realized != null)
  if (settled.length === 0) {
    return { profitFactor: null, winratePct: null, averageR: null }
  }

  let grossProfit = 0
  let grossLoss = 0
  let wins = 0

  for (const t of settled) {
    const pnl = t.realized ?? 0
    if (pnl >= 0) {
      grossProfit += pnl
      wins += 1
    } else {
      grossLoss += Math.abs(pnl)
    }
  }

  const withR = settled.filter((t) => t.r != null)
  const averageR =
    withR.length > 0 ? withR.reduce((sum, t) => sum + (t.r ?? 0), 0) / withR.length : null

  return {
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : null,
    winratePct: (wins / settled.length) * 100,
    averageR,
  }
}
