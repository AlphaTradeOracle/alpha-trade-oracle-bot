import { useState } from 'react'
import type { EquitySample } from '../../services/equityData'
import type { PortfolioSnapshot, Trade } from '../../types/trade'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'
import { EquityAnalysisChart } from './EquityAnalysisChart'
import { EquityStatistics } from './EquityStatistics'

interface EquityDetailsModalProps {
  open: boolean
  onClose: () => void
  portfolio: PortfolioSnapshot
  closedTrades: Trade[]
}

/**
 * Full equity workspace: interactive chart plus derived statistics.
 * Composition only — the chart and the statistics own their presentation.
 */
export function EquityDetailsModal({
  open,
  onClose,
  portfolio,
  closedTrades,
}: EquityDetailsModalProps) {
  const [samples, setSamples] = useState<EquitySample[]>([])

  if (!open) return null

  return (
    <Modal
      open
      size="xl"
      title="Equity Analyse"
      subtitle="Verlauf, Drawdown und Kennzahlen des Kontos"
      onClose={onClose}
      footer={
        <Button variant="primary" onClick={onClose}>
          Schließen
        </Button>
      }
    >
      <div className="space-y-4">
        <EquityAnalysisChart portfolio={portfolio} onSamplesChange={setSamples} />
        <EquityStatistics samples={samples} closedTrades={closedTrades} />
      </div>
    </Modal>
  )
}
