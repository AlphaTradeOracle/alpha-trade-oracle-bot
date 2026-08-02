import { useState } from 'react'
import { ClosedTradesTable } from '../components/trades/ClosedTradesTable'
import { TradeDetailsModal } from '../components/trades/detail'
import { TradeFilters } from '../components/trades/TradeFilters'
import { PageHeader } from '../components/ui/PageHeader'
import { DeskActions } from '../components/ui/DeskActions'
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
        subtitle={`${filtered.length} abgeschlossene Trades`}
        actions={<DeskActions />}
      />
      <TradeFilters
        filters={filters}
        onChange={setFilters}
        sortOptions={[
          { value: 'closedAt', label: 'Closed' },
          { value: 'symbol', label: 'Symbol' },
          { value: 'score', label: 'Score' },
          { value: 'realized', label: 'PnL' },
          { value: 'r', label: 'Profit %' },
        ]}
      />
      <ClosedTradesTable trades={filtered} onRowClick={setSelected} />
      <TradeDetailsModal trade={selected} onClose={() => setSelected(null)} />
    </div>
  )
}
