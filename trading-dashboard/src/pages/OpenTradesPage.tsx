import { useState } from 'react'
import { OpenTradesTable } from '../components/trades/OpenTradesTable'
import { TradeDetailsModal } from '../components/trades/detail'
import { TradeFilters } from '../components/trades/TradeFilters'
import { PageHeader } from '../components/ui/PageHeader'
import { DeskActions } from '../components/ui/DeskActions'
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
        subtitle={`${filtered.length} offene Positionen`}
        actions={<DeskActions />}
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
