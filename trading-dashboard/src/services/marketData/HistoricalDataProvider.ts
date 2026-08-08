import type { Candle } from '../../types/trade'
import {
  INTERVAL_SECONDS,
  alignToInterval,
  type CandleInterval,
  type MarketDataProvider,
} from './types'

export interface HistoricalDataProviderOptions {
  symbol: string
  interval: CandleInterval
  provider: MarketDataProvider
  /** Bars fetched per lazy-load step */
  pageSize?: number
  /** Upper bound of bars kept in memory */
  maxBars?: number
}

/**
 * Keeps a sorted candle window in memory and extends it on demand.
 *
 * The chart only asks for "more history" — where the data comes from (mock,
 * REST paging, WebSocket backfill) is entirely this class's concern.
 */
export class HistoricalDataProvider {
  readonly symbol: string
  readonly interval: CandleInterval

  private readonly provider: MarketDataProvider
  private readonly step: number
  private readonly pageSize: number
  private readonly maxBars: number

  /** Candles keyed by open time so merges stay idempotent. */
  private readonly byTime = new Map<number, Candle>()
  private oldestLoaded: number | null = null
  private newestLoaded: number | null = null
  private inFlight: Promise<Candle[]> | null = null
  private hitFloor = false

  constructor({
    symbol,
    interval,
    provider,
    pageSize = 500,
    maxBars = 20_000,
  }: HistoricalDataProviderOptions) {
    this.symbol = symbol
    this.interval = interval
    this.provider = provider
    this.step = INTERVAL_SECONDS[interval]
    this.pageSize = pageSize
    this.maxBars = maxBars
  }

  /** True once the source cannot deliver anything older. */
  get exhausted(): boolean {
    if (this.hitFloor) return true
    const earliest = this.provider.earliestTime
    if (earliest == null || this.oldestLoaded == null) return false
    return this.oldestLoaded <= alignToInterval(earliest, this.interval)
  }

  get candles(): Candle[] {
    return [...this.byTime.values()].sort((a, b) => a.time - b.time)
  }

  /** Loads the initial window centred on the given range. */
  async loadInitial(from: number, to: number): Promise<Candle[]> {
    return this.fetch(from, to)
  }

  /** Extends the window into the past by roughly one page. */
  async loadOlder(): Promise<Candle[]> {
    if (this.oldestLoaded == null || this.exhausted) return this.candles
    const to = this.oldestLoaded - this.step
    const from = to - this.pageSize * this.step
    return this.fetch(from, to)
  }

  /** Extends the window towards now (used by live updates later). */
  async loadNewer(until: number): Promise<Candle[]> {
    if (this.newestLoaded == null) return this.candles
    if (until <= this.newestLoaded) return this.candles
    return this.fetch(this.newestLoaded + this.step, until)
  }

  private async fetch(from: number, to: number): Promise<Candle[]> {
    // Serialise requests so overlapping scroll events cannot interleave.
    if (this.inFlight) await this.inFlight

    const request = this.provider.getCandles({
      symbol: this.symbol,
      interval: this.interval,
      from,
      to,
    })
    this.inFlight = request

    try {
      const page = await request
      page.forEach((candle) => this.byTime.set(candle.time, candle))

      if (page.length > 0) {
        const first = page[0].time
        const last = page[page.length - 1].time
        this.oldestLoaded = this.oldestLoaded == null ? first : Math.min(this.oldestLoaded, first)
        this.newestLoaded = this.newestLoaded == null ? last : Math.max(this.newestLoaded, last)
      } else {
        // Nothing came back — treat the requested edge as the history floor.
        this.hitFloor = true
        if (this.oldestLoaded != null) {
          this.oldestLoaded = Math.min(this.oldestLoaded, alignToInterval(from, this.interval))
        }
      }

      this.trim()
      return this.candles
    } finally {
      this.inFlight = null
    }
  }

  /** Drops the newest bars first — history is what users scroll towards. */
  private trim(): void {
    if (this.byTime.size <= this.maxBars) return
    const times = [...this.byTime.keys()].sort((a, b) => a - b)
    const overflow = times.length - this.maxBars
    times.slice(times.length - overflow).forEach((t) => this.byTime.delete(t))
    this.newestLoaded = times[times.length - overflow - 1] ?? this.newestLoaded
  }
}
