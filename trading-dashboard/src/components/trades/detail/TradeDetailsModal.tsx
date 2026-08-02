import { TradeChart } from '../../charts/trade'
import { Button } from '../../ui/Button'
import { Modal } from '../../ui/Modal'
import type { Trade } from '../../../types/trade'
import { TradeMarketContext } from './TradeMarketContext'
import { TradePerformance } from './TradePerformance'
import { TradeSummary } from './TradeSummary'
import { TradeTimeline } from './TradeTimeline'

interface TradeDetailsModalProps {
  trade: Trade | null
  onClose: () => void
}

/**
 * Trade detail workspace: chart, contract facts, performance, timeline.
 */
export function TradeDetailsModal({ trade, onClose }: TradeDetailsModalProps) {
  if (!trade) return null

  return (
    <Modal
      open
      size="xl"
      title={`${trade.symbol} · ${trade.side}`}
      subtitle={`${trade.strategy ?? 'Strategie unbekannt'} · Trade ${trade.id} · ${trade.status}`}
      onClose={onClose}
      footer={
        <Button variant="primary" onClick={onClose}>
          Schließen
        </Button>
      }
    >
      <div className="space-y-4">
        <TradeChart trade={trade} />

        <div className="grid gap-4 xl:grid-cols-3">
          <div className="xl:col-span-2">
            <TradeSummary trade={trade} />
          </div>
          <div className="space-y-4">
            <TradePerformance trade={trade} />
            <TradeMarketContext trade={trade} />
            <TradeTimeline trade={trade} />
          </div>
        </div>
      </div>
    </Modal>
  )
}
