import { OpenTradesTable } from '../components/trades/OpenTradesTable'
import { TradeFilters } from '../components/trades/TradeFilters'
import { PageHeader } from '../components/ui/PageHeader'
import { useTradeFilters } from '../hooks/useTradeFilters'
import { useTrades } from '../hooks/useTrades'

export function OpenTradesPage() {
  const { trades } = useTrades('OPEN')
  const { filters, setFilters, filtered } = useTradeFilters(trades, 'openedAt')

  return (
    <div>
      <PageHeader
        title="Open Trades"
        subtitle={`${filtered.length} active positions in the mock book`}
      />
      <TradeFilters
        filters={filters}
        onChange={setFilters}
        sortOptions={[
          { value: 'openedAt', label: 'Opened' },
          { value: 'symbol', label: 'Symbol' },
          { value: 'score', label: 'Score' },
          { value: 'upnl', label: 'uPnL' },
          { value: 'r', label: 'R' },
        ]}
      />
      <OpenTradesTable trades={filtered} />
    </div>
  )
}
