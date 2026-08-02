/** Local prototype settings — later mapped to user preferences / API. */
export interface AppSettings {
  exchange: 'binance' | 'bybit' | 'hyperliquid' | 'mock'
  refreshSeconds: number
  autoRefresh: boolean
  discordWebhook: string
  telegramEnabled: boolean
  theme: 'dark'
  defaultRiskR: number
}
