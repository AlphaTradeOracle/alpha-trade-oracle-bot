import { PendingTradesTable } from '../components/trades/PendingTradesTable'
import { TradeFilters } from '../components/trades/TradeFilters'
import { PageHeader } from '../components/ui/PageHeader'
import { useTradeFilters } from '../hooks/useTradeFilters'
import { useTrades } from '../hooks/useTrades'

export function PendingTradesPage() {
  const { trades } = useTrades('PENDING')
  const { filters, setFilters, filtered } = useTradeFilters(trades, 'openedAt')

  return (
    <div>
      <PageHeader
        title="Pending Trades"
        subtitle={`${filtered.length} waiting for retest / entry zone`}
      />
      <TradeFilters filters={filters} onChange={setFilters} showPnlFilter={false} />
      <PendingTradesTable trades={filtered} />
    </div>
  )
}
