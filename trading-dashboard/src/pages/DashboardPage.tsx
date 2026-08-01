import { useState } from 'react'
import { EquityChart } from '../components/charts/EquityChart'
import { KpiGrid } from '../components/kpi/KpiGrid'
import { ClosedTradesTable } from '../components/trades/ClosedTradesTable'
import { OpenTradesTable } from '../components/trades/OpenTradesTable'
import { PendingTradesTable } from '../components/trades/PendingTradesTable'
import { TradeDetailModal } from '../components/trades/TradeDetailModal'
import { TradeFilters } from '../components/trades/TradeFilters'
import { Button } from '../components/ui/Button'
import { Modal } from '../components/ui/Modal'
import { PageHeader } from '../components/ui/PageHeader'
import { PrototypeActions } from '../components/ui/PrototypeActions'
import { PrototypeBanner } from '../components/ui/PrototypeBanner'
import { usePortfolio } from '../hooks/usePortfolio'
import { useTradeFilters } from '../hooks/useTradeFilters'
import { useTrades } from '../hooks/useTrades'
import type { Trade } from '../types/trade'

export function DashboardPage() {
  const { portfolio, equity } = usePortfolio()
  const { trades: open } = useTrades('OPEN')
  const { trades: pending } = useTrades('PENDING')
  const { trades: closed } = useTrades('CLOSED')

  const openFilters = useTradeFilters(open, 'openedAt')
  const pendingFilters = useTradeFilters(pending, 'openedAt')
  const closedFilters = useTradeFilters(closed, 'closedAt')

  const [selected, setSelected] = useState<Trade | null>(null)
  const [kpiTitle, setKpiTitle] = useState<string | null>(null)

  return (
    <div className="space-y-8">
      <PageHeader
        title="Trading Dashboard"
        subtitle="Interactive MVP · mock book from JSON · click KPIs, rows, and actions"
        actions={<PrototypeActions context="Dashboard" />}
      />

      <KpiGrid portfolio={portfolio} onKpiClick={setKpiTitle} />

      <EquityChart data={equity} />

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-base font-semibold">Open Trades</h2>
          <span className="text-xs text-[var(--color-text-muted)]">
            {openFilters.filtered.length} shown · click row for details
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
        <OpenTradesTable trades={openFilters.filtered} onRowClick={setSelected} />
      </section>

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-base font-semibold">Pending Trades</h2>
          <span className="text-xs text-[var(--color-text-muted)]">
            {pendingFilters.filtered.length} shown · click row for details
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
            {closedFilters.filtered.length} shown · click row for details
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
        <ClosedTradesTable trades={closedFilters.filtered} onRowClick={setSelected} />
      </section>

      <TradeDetailModal trade={selected} onClose={() => setSelected(null)} />

      <Modal
        open={kpiTitle !== null}
        title={kpiTitle ?? 'KPI'}
        onClose={() => setKpiTitle(null)}
        footer={
          <Button variant="primary" onClick={() => setKpiTitle(null)}>
            Got it
          </Button>
        }
      >
        <div className="space-y-3">
          <PrototypeBanner>
            KPI drill-down is a UI placeholder. Later this can deep-link into Analytics.
          </PrototypeBanner>
          <p>
            Selected metric: <span className="text-[var(--color-text)]">{kpiTitle}</span>
          </p>
        </div>
      </Modal>
    </div>
  )
}
