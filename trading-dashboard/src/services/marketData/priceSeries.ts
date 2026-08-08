/**
 * Deterministic, time-continuous synthetic price curve.
 *
 * Every price is a pure function of (symbol, timestamp), so chunks fetched at
 * different moments — or at different zoom levels — always line up. That is
 * what makes lazy loading of older candles seamless without a real backend.
 */

function hash2(a: number, b: number): number {
  let h = 2166136261 ^ Math.imul(a | 0, 374761393)
  h = Math.imul(h ^ (b | 0), 668265263)
  h ^= h >>> 15
  h = Math.imul(h, 2246822519)
  h ^= h >>> 13
  return (h >>> 0) / 4294967295
}

export function hashString(value: string): number {
  let h = 2166136261
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

/** Smoothstep keeps the interpolated noise free of visible kinks. */
function smooth(t: number): number {
  return t * t * (3 - 2 * t)
}

/** Value noise sampled on a grid of `period` seconds. */
function valueNoise(seed: number, time: number, period: number): number {
  const x = time / period
  const i = Math.floor(x)
  const f = x - i
  const a = hash2(seed, i)
  const b = hash2(seed, i + 1)
  return (a + (b - a) * smooth(f)) * 2 - 1
}

/** Anchor the curve to known trade prices at known times. */
export interface PriceAnchor {
  time: number
  price: number
}

export interface PriceCurveOptions {
  symbol: string
  anchors: PriceAnchor[]
  /** Relative amplitude of the random walk (0.05 ≈ ±5 %) */
  amplitude?: number
}

export interface PriceCurve {
  /** Mid price at an arbitrary timestamp */
  at: (time: number) => number
  /** Deterministic 0..1 sample used for wick/volume jitter */
  jitter: (time: number, salt: number) => number
}

/**
 * Builds a curve that passes exactly through the given anchors while drifting
 * plausibly everywhere else.
 */
export function createPriceCurve({
  symbol,
  anchors,
  amplitude = 0.055,
}: PriceCurveOptions): PriceCurve {
  const seed = hashString(symbol)
  const sorted = [...anchors].sort((a, b) => a.time - b.time)
  const fallback = sorted[0]?.price ?? 100

  // Multi-octave noise: slow trend plus faster intraday movement.
  const octaves = [
    { period: 86_400 * 9, weight: 1 },
    { period: 86_400 * 2.5, weight: 0.55 },
    { period: 3_600 * 8, weight: 0.28 },
    { period: 3_600 * 2, weight: 0.14 },
    { period: 600, weight: 0.07 },
  ]
  const totalWeight = octaves.reduce((sum, o) => sum + o.weight, 0)

  const rawNoise = (time: number): number => {
    let acc = 0
    for (let i = 0; i < octaves.length; i += 1) {
      acc += valueNoise(seed + i * 7919, time, octaves[i].period) * octaves[i].weight
    }
    return (acc / totalWeight) * amplitude
  }

  /** Linear interpolation of a value defined at the anchor times. */
  const interpolateAtAnchors = (time: number, valueOf: (a: PriceAnchor) => number): number => {
    if (sorted.length === 0) return 0
    if (sorted.length === 1 || time <= sorted[0].time) return valueOf(sorted[0])
    const last = sorted[sorted.length - 1]
    if (time >= last.time) return valueOf(last)
    for (let i = 1; i < sorted.length; i += 1) {
      const prev = sorted[i - 1]
      const next = sorted[i]
      if (time <= next.time) {
        const span = next.time - prev.time || 1
        const ratio = (time - prev.time) / span
        return valueOf(prev) + (valueOf(next) - valueOf(prev)) * ratio
      }
    }
    return valueOf(last)
  }

  // Cancel the noise exactly at the anchors so entry/exit prices are hit.
  const noiseAtAnchor = new Map<number, number>()
  sorted.forEach((a) => noiseAtAnchor.set(a.time, rawNoise(a.time)))

  const baseline = (time: number): number =>
    sorted.length === 0 ? fallback : interpolateAtAnchors(time, (a) => a.price)

  const correction = (time: number): number =>
    interpolateAtAnchors(time, (a) => noiseAtAnchor.get(a.time) ?? 0)

  return {
    at(time: number) {
      const base = baseline(time)
      const factor = 1 + rawNoise(time) - correction(time)
      return Math.max(base * factor, base * 0.05)
    },
    jitter(time: number, salt: number) {
      return hash2(seed + salt * 104729, Math.floor(time))
    },
  }
}
