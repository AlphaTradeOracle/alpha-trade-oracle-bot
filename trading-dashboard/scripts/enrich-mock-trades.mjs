/**
 * One-off mock-data tooling.
 *
 * Adds detail-view fields (strategy, take-profit ladder, leverage, fees, …)
 * to `src/data/trades.json` in a deterministic way, so the prototype shows
 * a coherent book. Re-running produces identical output.
 *
 * Usage: node scripts/enrich-mock-trades.mjs
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const file = resolve(here, '../src/data/trades.json')

const STRATEGIES = [
  'MTF Momentum',
  'Trend Continuation',
  'Range Reversal',
  'Breakout Retest',
  'Mean Reversion',
]

/** Small deterministic hash so every trade keeps the same generated values. */
function hash(str) {
  let h = 2166136261
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return Math.abs(h)
}

function round(value, ref) {
  const digits = ref >= 100 ? 2 : ref >= 1 ? 4 : 6
  return Number(value.toFixed(digits))
}

const trades = JSON.parse(readFileSync(file, 'utf8'))

const enriched = trades.map((t) => {
  const seed = hash(t.id + t.symbol)
  const dir = t.side === 'LONG' ? 1 : -1
  const risk = Math.abs(t.entry - t.stop)

  // R-multiple ladder; a subset of trades carries a fourth target.
  const ladder = seed % 3 === 0 ? [1, 2, 3, 4] : [1, 2, 3]
  const takeProfits = ladder.map((mult, i) => {
    const price = round(t.entry + dir * risk * mult, t.entry)
    const reached =
      t.status === 'CLOSED' && t.exit != null
        ? dir === 1
          ? t.exit >= price
          : t.exit <= price
        : false
    return {
      label: `TP${i + 1}`,
      price,
      size: Number((1 / ladder.length).toFixed(2)),
      hit: reached,
    }
  })

  const leverage = [3, 5, 10, 20][seed % 4]
  const positionSize = Number(((t.margin * leverage) / t.entry).toFixed(4))
  const fees = Number((t.margin * leverage * 0.0006).toFixed(2))

  return {
    ...t,
    strategy: STRATEGIES[seed % STRATEGIES.length],
    takeProfits,
    positionSize,
    leverage,
    fees,
    notes: '',
  }
})

writeFileSync(file, `${JSON.stringify(enriched, null, 2)}\n`)
console.log(`Enriched ${enriched.length} mock trades.`)
