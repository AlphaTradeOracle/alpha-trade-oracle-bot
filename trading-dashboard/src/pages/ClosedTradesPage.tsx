import { ClosedTradesTable } from '../components/trades/ClosedTradesTable'
import { TradeFilters } from '../components/trades/TradeFilters'
import { PageHeader } from '../components/ui/PageHeader'
import { useTradeFilters } from '../hooks/useTradeFilters'
import { useTrades } from '../hooks/useTrades'

export function ClosedTradesPage() {
  const { trades } = useTrades('CLOSED')
  const { filters, setFilters, filtered } = useTradeFilters(trades, 'closedAt')

  return (
    <div>
      <PageHeader
        title="Closed Trades"
        subtitle={`${filtered.length} historical fills in the mock ledger`}
      />
      <TradeFilters
        filters={filters}
        onChange={setFilters}
        sortOptions={[
          { value: 'closedAt', label: 'Closed' },
          { value: 'symbol', label: 'Symbol' },
          { value: 'score', label: 'Score' },
          { value: 'realized', label: 'PnL' },
          { value: 'r', label: 'R' },
        ]}
      />
      <ClosedTradesTable trades={filtered} />
    </div>
  )
}
