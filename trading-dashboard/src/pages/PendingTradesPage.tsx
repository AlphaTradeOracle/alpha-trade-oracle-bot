import { useState } from 'react'
import { PendingTradesTable } from '../components/trades/PendingTradesTable'
import { TradeDetailModal } from '../components/trades/TradeDetailModal'
import { TradeFilters } from '../components/trades/TradeFilters'
import { PageHeader } from '../components/ui/PageHeader'
import { PrototypeActions } from '../components/ui/PrototypeActions'
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
        subtitle={`${filtered.length} waiting for retest / entry zone · click a row`}
        actions={<PrototypeActions context="Pending Trades" />}
      />
      <TradeFilters filters={filters} onChange={setFilters} showPnlFilter={false} />
      <PendingTradesTable trades={filtered} onRowClick={setSelected} />
      <TradeDetailModal trade={selected} onClose={() => setSelected(null)} />
    </div>
  )
}
