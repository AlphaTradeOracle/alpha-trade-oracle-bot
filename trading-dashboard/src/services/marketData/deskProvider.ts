import type { Candle } from '../../types/trade'
import { apiBase } from '../deskApi'
import type { CandleRequest, MarketDataProvider } from './types'

/** Candle source backed by `/api/v1/desk/candles` (exchange data via the bot). */
export function createDeskMarketData(): MarketDataProvider {
  return {
    id: 'desk',
    async getCandles({ symbol, interval, from, to }: CandleRequest): Promise<Candle[]> {
      const params = new URLSearchParams({
        symbol,
        interval,
        from: String(Math.max(0, Math.floor(from))),
        to: String(Math.max(0, Math.floor(to))),
      })
      const response = await fetch(`${apiBase()}/api/v1/desk/candles?${params}`, {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      })
      if (!response.ok) {
        const detail = await response.text().catch(() => '')
        throw new Error(
          detail
            ? `Candles ${response.status}: ${detail.slice(0, 180)}`
            : `Candles ${response.status}`,
        )
      }
      const rows = (await response.json()) as Array<{
        time: number
        open: number
        high: number
        low: number
        close: number
        volume?: number | null
      }>
      return rows
        .map((row) => ({
          time: row.time,
          open: row.open,
          high: row.high,
          low: row.low,
          close: row.close,
          volume: row.volume ?? undefined,
        }))
        .sort((a, b) => a.time - b.time)
    },
  }
}
