import {
  Activity,
  Banknote,
  Briefcase,
  CircleDollarSign,
  Clock3,
  Layers3,
  Lock,
  Percent,
  TrendingUp,
  Wallet,
} from 'lucide-react'
import { getKpiTooltip } from '../../config/kpiTooltips'
import type { EquityPoint, PortfolioSnapshot } from '../../types/trade'
import { formatMoney, formatPct, formatSignedMoney } from '../../utils/format'
import { KpiCard, type KpiTone } from './KpiCard'
import { PerformanceKpiCard } from './PerformanceKpiCard'

function toneFromNumber(n: number): KpiTone {
  if (n > 0) return 'positive'
  if (n < 0) return 'negative'
  return 'neutral'
}

interface KpiGridProps {
  portfolio: PortfolioSnapshot
  equity?: EquityPoint[]
  /** Hide live-looking numbers until the first desk snapshot arrives. */
  loading?: boolean
  onKpiClick?: (title: string) => void
}

export function KpiGrid({
  portfolio: p,
  equity = [],
  loading = false,
  onKpiClick,
}: KpiGridProps) {
  const totalRealized =
    p.accountRealizedPnl ?? p.realizedPnl + (p.openRealizedPnl ?? 0)
  const dash = '—'
  const items = [
    {
      title: 'Startkapital',
      value: loading ? dash : formatMoney(p.totalCapital),
      icon: Briefcase,
      tone: 'neutral' as KpiTone,
    },
    {
      title: 'Equity',
      value: loading ? dash : formatMoney(p.equity),
      icon: TrendingUp,
      tone: loading ? ('neutral' as KpiTone) : toneFromNumber(p.equity - p.totalCapital),
    },
    {
      title: 'Cash',
      value: loading ? dash : formatMoney(p.cash),
      icon: Wallet,
      tone: loading ? ('neutral' as KpiTone) : ('accent' as KpiTone),
    },
    {
      title: 'Realized PnL',
      value: loading ? dash : formatSignedMoney(totalRealized),
      icon: CircleDollarSign,
      tone: loading ? ('neutral' as KpiTone) : toneFromNumber(totalRealized),
    },
    {
      title: 'Total Return',
      value: loading ? dash : formatPct(p.totalReturnPct),
      icon: Percent,
      tone: loading ? ('neutral' as KpiTone) : toneFromNumber(p.totalReturnPct),
    },
    {
      title: 'Open uPnL',
      value: loading ? dash : formatSignedMoney(p.openUpnl),
      icon: Activity,
      tone: loading ? ('neutral' as KpiTone) : toneFromNumber(p.openUpnl),
    },
    {
      title: 'Winrate',
      value: loading || p.winRatePct == null ? dash : `${Number(p.winRatePct).toFixed(1)}%`,
      icon: Percent,
      tone:
        loading || p.winRatePct == null
          ? ('neutral' as KpiTone)
          : toneFromNumber(p.winRatePct - 50),
    },
    {
      title: 'Margin Locked',
      value: loading ? dash : formatMoney(p.marginLocked),
      icon: Lock,
      tone: 'neutral' as KpiTone,
    },
    {
      title: 'Open Positions',
      value: loading ? dash : String(p.openPositions),
      icon: Layers3,
      tone: 'neutral' as KpiTone,
    },
    {
      title: 'Pending Orders',
      value: loading ? dash : String(p.pendingOrders),
      icon: Clock3,
      tone: 'neutral' as KpiTone,
    },
    {
      title: 'Closed Trades',
      value: loading ? dash : String(p.closedTrades),
      icon: Banknote,
      tone: 'neutral' as KpiTone,
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-6">
      {items.map((item) => (
        <KpiCard
          key={item.title}
          {...item}
          tooltip={getKpiTooltip(item.title)}
          onClick={onKpiClick ? () => onKpiClick(item.title) : undefined}
        />
      ))}
      <PerformanceKpiCard equity={equity} loading={loading} />
    </div>
  )
}
