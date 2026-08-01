import { useState } from 'react'
import { ClosedTradesTable } from '../components/trades/ClosedTradesTable'
import { TradeDetailModal } from '../components/trades/TradeDetailModal'
import { TradeFilters } from '../components/trades/TradeFilters'
import { PageHeader } from '../components/ui/PageHeader'
import { PrototypeActions } from '../components/ui/PrototypeActions'
import { useTradeFilters } from '../hooks/useTradeFilters'
import { useTrades } from '../hooks/useTrades'
import type { Trade } from '../types/trade'

export function ClosedTradesPage() {
  const { trades } = useTrades('CLOSED')
  const { filters, setFilters, filtered } = useTradeFilters(trades, 'closedAt')
  const [selected, setSelected] = useState<Trade | null>(null)

  return (
    <div>
      <PageHeader
        title="Closed Trades"
        subtitle={`${filtered.length} historical fills · click a row for details`}
        actions={<PrototypeActions context="Closed Trades" showNewTrade={false} />}
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
      <ClosedTradesTable trades={filtered} onRowClick={setSelected} />
      <TradeDetailModal trade={selected} onClose={() => setSelected(null)} />
    </div>
  )
}
