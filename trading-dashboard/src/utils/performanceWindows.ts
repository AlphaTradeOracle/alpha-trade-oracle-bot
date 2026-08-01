import type { EquityPoint } from '../types/trade'

export interface PerformanceWindow {
  label: string
  /** Percent change vs baseline; null when equity curve is empty */
  pct: number | null
}

const WINDOWS: Array<{ label: string; ms: number }> = [
  { label: '1h', ms: 60 * 60 * 1000 },
  { label: '24h', ms: 24 * 60 * 60 * 1000 },
  { label: '7D', ms: 7 * 24 * 60 * 60 * 1000 },
  { label: '30D', ms: 30 * 24 * 60 * 60 * 1000 },
]

function toMs(iso: string): number {
  return new Date(iso).getTime()
}

/** Last equity at or before ``sinceMs``; falls back to the first sample. */
function baselineEquity(points: EquityPoint[], sinceMs: number): number | null {
  if (points.length === 0) return null
  let baseline: number | null = null
  for (const point of points) {
    const t = toMs(point.t)
    if (!Number.isFinite(t)) continue
    if (t <= sinceMs) baseline = point.equity
    else break
  }
  return baseline ?? points[0]?.equity ?? null
}

/** Equity % change over 1h / 24h / 7D / 30D from the desk fill curve. */
export function computePerformanceWindows(
  equity: EquityPoint[],
  nowMs: number = Date.now(),
): PerformanceWindow[] {
  if (equity.length === 0) {
    return WINDOWS.map(({ label }) => ({ label, pct: null }))
  }

  const latest = equity[equity.length - 1]
  const live = latest.equity
  if (!Number.isFinite(live) || live === 0) {
    return WINDOWS.map(({ label }) => ({ label, pct: null }))
  }

  return WINDOWS.map(({ label, ms }) => {
    const baseline = baselineEquity(equity, nowMs - ms)
    if (baseline == null || !Number.isFinite(baseline) || baseline === 0) {
      return { label, pct: null }
    }
    return { label, pct: ((live - baseline) / baseline) * 100 }
  })
}
