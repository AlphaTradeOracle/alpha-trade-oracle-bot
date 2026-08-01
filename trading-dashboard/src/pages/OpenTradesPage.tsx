import { useState } from 'react'
import { OpenTradesTable } from '../components/trades/OpenTradesTable'
import { TradeDetailsModal } from '../components/trades/detail'
import { TradeFilters } from '../components/trades/TradeFilters'
import { PageHeader } from '../components/ui/PageHeader'
import { PrototypeActions } from '../components/ui/PrototypeActions'
import { useTradeFilters } from '../hooks/useTradeFilters'
import { useTrades } from '../hooks/useTrades'
import type { Trade } from '../types/trade'

export function OpenTradesPage() {
  const { trades } = useTrades('OPEN')
  const { filters, setFilters, filtered } = useTradeFilters(trades, 'openedAt')
  const [selected, setSelected] = useState<Trade | null>(null)

  return (
    <div>
      <PageHeader
        title="Open Trades"
        subtitle={`${filtered.length} active positions · click a row for details`}
        actions={<PrototypeActions context="Open Trades" />}
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
      <OpenTradesTable trades={filtered} onRowClick={setSelected} />
      <TradeDetailsModal trade={selected} onClose={() => setSelected(null)} />
    </div>
  )
}
