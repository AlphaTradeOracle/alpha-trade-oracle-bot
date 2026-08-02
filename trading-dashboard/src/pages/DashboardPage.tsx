import { useState } from 'react'
import { EquityChart } from '../components/charts/EquityChart'
import { EquityDetailsModal } from '../components/equity'
import { KpiGrid } from '../components/kpi/KpiGrid'
import { MarketRegimeCard } from '../components/market/MarketRegimeCard'
import { TopCoinsBanner } from '../components/market/TopCoinsBanner'
import { ClosedTradesTable } from '../components/trades/ClosedTradesTable'
import { OpenTradesTable } from '../components/trades/OpenTradesTable'
import { PendingTradesTable } from '../components/trades/PendingTradesTable'
import { TradeDetailsModal } from '../components/trades/detail'
import { TradeFilters } from '../components/trades/TradeFilters'
import { DeskActions } from '../components/ui/DeskActions'
import { PageHeader } from '../components/ui/PageHeader'
import { useDeskData } from '../context/DeskDataContext'
import { usePortfolio } from '../hooks/usePortfolio'
import { useTradeFilters } from '../hooks/useTradeFilters'
import { useTrades } from '../hooks/useTrades'
import type { Trade } from '../types/trade'

export function DashboardPage() {
  const { portfolio, equity } = usePortfolio()
  const { marketRegime } = useDeskData()
  const { trades: open } = useTrades('OPEN')
  const { trades: pending } = useTrades('PENDING')
  const { trades: closed } = useTrades('CLOSED')

  const openFilters = useTradeFilters(open, 'openedAt')
  const pendingFilters = useTradeFilters(pending, 'openedAt')
  const closedFilters = useTradeFilters(closed, 'closedAt')

  const [selected, setSelected] = useState<Trade | null>(null)
  const [equityOpen, setEquityOpen] = useState(false)

  return (
    <div className="space-y-8">
      <TopCoinsBanner />

      <PageHeader
        title="Trading Dashboard"
        subtitle="Überblick über Kapital, offene Positionen und Handelshistorie"
        actions={<DeskActions />}
      />

      <KpiGrid portfolio={portfolio} equity={equity} />

      <MarketRegimeCard regime={marketRegime} />

      <EquityChart data={equity} onOpenDetails={() => setEquityOpen(true)} />

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-base font-semibold">Open Trades</h2>
          <span className="text-xs text-[var(--color-text-muted)]">
            {openFilters.filtered.length} Positionen
          </span>
        </div>
        <TradeFilters
          filters={openFilters.filters}
          onChange={openFilters.setFilters}
          sortOptions={[
            { value: 'openedAt', label: 'Opened' },
            { value: 'symbol', label: 'Symbol' },
            { value: 'score', label: 'Score' },
            { value: 'upnl', label: 'uPnL' },
            { value: 'r', label: 'Profit %' },
          ]}
        />
        <OpenTradesTable trades={openFilters.filtered} onRowClick={setSelected} />
      </section>

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-base font-semibold">Pending Trades</h2>
          <span className="text-xs text-[var(--color-text-muted)]">
            {pendingFilters.filtered.length} Orders
          </span>
        </div>
        <TradeFilters
          filters={pendingFilters.filters}
          onChange={pendingFilters.setFilters}
          showPnlFilter={false}
        />
        <PendingTradesTable trades={pendingFilters.filtered} onRowClick={setSelected} />
      </section>

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-base font-semibold">Closed Trades</h2>
          <span className="text-xs text-[var(--color-text-muted)]">
            {closedFilters.filtered.length} Trades
          </span>
        </div>
        <TradeFilters
          filters={closedFilters.filters}
          onChange={closedFilters.setFilters}
          sortOptions={[
            { value: 'closedAt', label: 'Closed' },
            { value: 'symbol', label: 'Symbol' },
            { value: 'score', label: 'Score' },
            { value: 'realized', label: 'PnL' },
            { value: 'r', label: 'Profit %' },
          ]}
        />
        <ClosedTradesTable trades={closedFilters.filtered} onRowClick={setSelected} />
      </section>

      <TradeDetailsModal trade={selected} onClose={() => setSelected(null)} />

      <EquityDetailsModal
        open={equityOpen}
        onClose={() => setEquityOpen(false)}
        portfolio={portfolio}
        equityPoints={equity}
        closedTrades={closed}
      />
    </div>
  )
}
