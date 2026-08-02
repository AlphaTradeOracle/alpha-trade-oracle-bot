import { INTERVAL_SECONDS, alignToInterval, type CandleInterval } from '../marketData'
import type { EquityDataProvider, EquitySample } from './types'

export interface HistoricalEquityProviderOptions {
  interval: CandleInterval
  provider: EquityDataProvider
  /** Points fetched per lazy-load step */
  pageSize?: number
  /** Upper bound of points kept in memory */
  maxPoints?: number
}

/**
 * Keeps a sorted equity window in memory and extends it on demand.
 *
 * The chart only asks for "more history" — whether that comes from the mock
 * curve, a paged REST endpoint or a WebSocket backfill is this class's concern.
 */
export class HistoricalEquityProvider {
  readonly interval: CandleInterval

  private readonly provider: EquityDataProvider
  private readonly step: number
  private readonly pageSize: number
  private readonly maxPoints: number

  private readonly byTime = new Map<number, EquitySample>()
  private oldestLoaded: number | null = null
  private newestLoaded: number | null = null
  private inFlight: Promise<EquitySample[]> | null = null

  constructor({
    interval,
    provider,
    pageSize = 600,
    maxPoints = 50_000,
  }: HistoricalEquityProviderOptions) {
    this.interval = interval
    this.provider = provider
    this.step = INTERVAL_SECONDS[interval]
    this.pageSize = pageSize
    this.maxPoints = maxPoints
  }

  get exhausted(): boolean {
    const earliest = this.provider.earliestTime
    if (earliest == null || this.oldestLoaded == null) return false
    return this.oldestLoaded <= alignToInterval(earliest, this.interval)
  }

  get samples(): EquitySample[] {
    return [...this.byTime.values()].sort((a, b) => a.time - b.time)
  }

  async loadInitial(from: number, to: number): Promise<EquitySample[]> {
    return this.fetch(from, to)
  }

  async loadOlder(): Promise<EquitySample[]> {
    if (this.oldestLoaded == null || this.exhausted) return this.samples
    const to = this.oldestLoaded - this.step
    const from = to - this.pageSize * this.step
    return this.fetch(from, to)
  }

  private async fetch(from: number, to: number): Promise<EquitySample[]> {
    if (this.inFlight) await this.inFlight

    const request = this.provider.getSeries({ interval: this.interval, from, to })
    this.inFlight = request

    try {
      const page = await request
      page.forEach((sample) => this.byTime.set(sample.time, sample))

      if (page.length > 0) {
        const first = page[0].time
        const last = page[page.length - 1].time
        this.oldestLoaded = this.oldestLoaded == null ? first : Math.min(this.oldestLoaded, first)
        this.newestLoaded = this.newestLoaded == null ? last : Math.max(this.newestLoaded, last)
      } else if (this.oldestLoaded != null) {
        this.oldestLoaded = Math.min(this.oldestLoaded, alignToInterval(from, this.interval))
      }

      this.trim()
      return this.samples
    } finally {
      this.inFlight = null
    }
  }

  /** Drops the newest points first — history is what users scroll towards. */
  private trim(): void {
    if (this.byTime.size <= this.maxPoints) return
    const times = [...this.byTime.keys()].sort((a, b) => a - b)
    const overflow = times.length - this.maxPoints
    times.slice(times.length - overflow).forEach((t) => this.byTime.delete(t))
    this.newestLoaded = times[times.length - overflow - 1] ?? this.newestLoaded
  }
}
