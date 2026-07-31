import { EquityChart } from '../components/charts/EquityChart'
import { KpiGrid } from '../components/kpi/KpiGrid'
import { ClosedTradesTable } from '../components/trades/ClosedTradesTable'
import { OpenTradesTable } from '../components/trades/OpenTradesTable'
import { PendingTradesTable } from '../components/trades/PendingTradesTable'
import { TradeFilters } from '../components/trades/TradeFilters'
import { PageHeader } from '../components/ui/PageHeader'
import { usePortfolio } from '../hooks/usePortfolio'
import { useTradeFilters } from '../hooks/useTradeFilters'
import { useTrades } from '../hooks/useTrades'

export function DashboardPage() {
  const { portfolio, equity } = usePortfolio()
  const { trades: open } = useTrades('OPEN')
  const { trades: pending } = useTrades('PENDING')
  const { trades: closed } = useTrades('CLOSED')

  const openFilters = useTradeFilters(open, 'openedAt')
  const pendingFilters = useTradeFilters(pending, 'openedAt')
  const closedFilters = useTradeFilters(closed, 'closedAt')

  return (
    <div className="space-y-8">
      <PageHeader
        title="Trading Dashboard"
        subtitle="Local desk overview · mock book loaded from JSON"
      />

      <KpiGrid portfolio={portfolio} />

      <EquityChart data={equity} />

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-base font-semibold">Open Trades</h2>
          <span className="text-xs text-[var(--color-text-muted)]">
            {openFilters.filtered.length} shown
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
            { value: 'r', label: 'R' },
          ]}
        />
        <OpenTradesTable trades={openFilters.filtered} />
      </section>

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-base font-semibold">Pending Trades</h2>
          <span className="text-xs text-[var(--color-text-muted)]">
            {pendingFilters.filtered.length} shown
          </span>
        </div>
        <TradeFilters
          filters={pendingFilters.filters}
          onChange={pendingFilters.setFilters}
          showPnlFilter={false}
        />
        <PendingTradesTable trades={pendingFilters.filtered} />
      </section>

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-base font-semibold">Closed Trades</h2>
          <span className="text-xs text-[var(--color-text-muted)]">
            {closedFilters.filtered.length} shown
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
            { value: 'r', label: 'R' },
          ]}
        />
        <ClosedTradesTable trades={closedFilters.filtered} />
      </section>
    </div>
  )
}
