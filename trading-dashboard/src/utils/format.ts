/** Display helpers — keep UI formatting out of components. */

const moneyFmt = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const compactMoneyFmt = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  notation: 'compact',
  maximumFractionDigits: 2,
})

export function formatMoney(value: number, compact = false): string {
  return (compact ? compactMoneyFmt : moneyFmt).format(value)
}

export function formatSignedMoney(value: number): string {
  if (value > 0) return `+${moneyFmt.format(value)}`
  return moneyFmt.format(value)
}

export function formatPct(value: number, digits = 2): string {
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}%`
}

export function formatR(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}R`
}

/** Margin return in percent: PnL / margin × 100 (falls back to R×100). */
export function tradeProfitPct(trade: {
  status: string
  margin: number
  realized: number | null
  upnl: number | null
  r: number | null
}): number | null {
  if (trade.margin > 0) {
    const pnl = trade.status === 'CLOSED' ? trade.realized : trade.upnl
    if (pnl != null && Number.isFinite(pnl)) {
      return (pnl / trade.margin) * 100
    }
  }
  if (trade.r != null && Number.isFinite(trade.r)) {
    return trade.r * 100
  }
  return null
}

export function formatPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  if (value >= 100) return value.toFixed(2)
  if (value >= 1) return value.toFixed(4)
  return value.toFixed(6)
}

export function formatDuration(openedAt: string, closedAt: string | null): string {
  const start = new Date(openedAt).getTime()
  const end = closedAt ? new Date(closedAt).getTime() : Date.now()
  const mins = Math.max(0, Math.round((end - start) / 60_000))
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  const rem = mins % 60
  if (hours < 48) return rem ? `${hours}h ${rem}m` : `${hours}h`
  const days = Math.floor(hours / 24)
  const rh = hours % 24
  return rh ? `${days}d ${rh}h` : `${days}d`
}

export function formatSince(iso: string): string {
  return formatDuration(iso, null)
}

/** Single locale for every date shown in the UI. */
const LOCALE = 'de-DE'

const dateTimeFmt = new Intl.DateTimeFormat(LOCALE, {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

const dateFmt = new Intl.DateTimeFormat(LOCALE, {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
})

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return dateTimeFmt.format(d)
}

/** Chart tooltips work with unix seconds instead of ISO strings. */
export function formatTimestamp(unixSeconds: number, withTime = true): string {
  const d = new Date(unixSeconds * 1000)
  if (Number.isNaN(d.getTime())) return '—'
  return withTime ? dateTimeFmt.format(d) : dateFmt.format(d)
}
