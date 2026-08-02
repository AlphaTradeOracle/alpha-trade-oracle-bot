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
  onKpiClick?: (title: string) => void
}

export function KpiGrid({ portfolio: p, equity = [], onKpiClick }: KpiGridProps) {
  const items = [
    {
      title: 'Startkapital',
      value: formatMoney(p.totalCapital),
      icon: Briefcase,
      tone: 'neutral' as KpiTone,
    },
    {
      title: 'Equity',
      value: formatMoney(p.equity),
      icon: TrendingUp,
      tone: toneFromNumber(p.equity - p.totalCapital),
    },
    {
      title: 'Total Return',
      value: formatPct(p.totalReturnPct),
      icon: Percent,
      tone: toneFromNumber(p.totalReturnPct),
    },
    {
      title: 'Open uPnL',
      value: formatSignedMoney(p.openUpnl),
      icon: Activity,
      tone: toneFromNumber(p.openUpnl),
    },
    {
      title: 'Winrate',
      value: p.winRatePct != null ? `${Number(p.winRatePct).toFixed(1)}%` : '—',
      icon: Percent,
      tone:
        p.winRatePct != null ? toneFromNumber(p.winRatePct - 50) : ('neutral' as KpiTone),
    },
    {
      title: 'Cash',
      value: formatMoney(p.cash),
      icon: Wallet,
      tone: 'accent' as KpiTone,
    },
    {
      title: 'Margin Locked',
      value: formatMoney(p.marginLocked),
      icon: Lock,
      tone: 'neutral' as KpiTone,
    },
    {
      title: 'Realized PnL',
      value: formatSignedMoney(p.realizedPnl),
      icon: CircleDollarSign,
      tone: toneFromNumber(p.realizedPnl),
    },
    {
      title: 'Open Positions',
      value: String(p.openPositions),
      icon: Layers3,
      tone: 'neutral' as KpiTone,
    },
    {
      title: 'Pending Orders',
      value: String(p.pendingOrders),
      icon: Clock3,
      tone: 'neutral' as KpiTone,
    },
    {
      title: 'Closed Trades',
      value: String(p.closedTrades),
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
          onClick={onKpiClick ? () => onKpiClick(item.title) : undefined}
        />
      ))}
      <PerformanceKpiCard equity={equity} />
    </div>
  )
}
