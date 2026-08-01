import { useState } from 'react'
import { PendingTradesTable } from '../components/trades/PendingTradesTable'
import { TradeDetailsModal } from '../components/trades/detail'
import { TradeFilters } from '../components/trades/TradeFilters'
import { PageHeader } from '../components/ui/PageHeader'
import { DeskActions } from '../components/ui/DeskActions'
import { useTradeFilters } from '../hooks/useTradeFilters'
import { useTrades } from '../hooks/useTrades'
import type { Trade } from '../types/trade'

export function PendingTradesPage() {
  const { trades } = useTrades('PENDING')
  const { filters, setFilters, filtered } = useTradeFilters(trades, 'openedAt')
  const [selected, setSelected] = useState<Trade | null>(null)

  return (
    <div>
      <PageHeader
        title="Pending Trades"
        subtitle={`${filtered.length} Orders warten auf die Entry-Zone`}
        actions={<DeskActions context="Pending Trades" />}
      />
      <TradeFilters filters={filters} onChange={setFilters} showPnlFilter={false} />
      <PendingTradesTable trades={filtered} onRowClick={setSelected} />
      <TradeDetailsModal trade={selected} onClose={() => setSelected(null)} />
    </div>
  )
}
