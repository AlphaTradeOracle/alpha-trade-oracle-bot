import { useState } from 'react'
import { TradeChart } from '../../charts/trade'
import { Button } from '../../ui/Button'
import { Modal } from '../../ui/Modal'
import { PrototypeBanner } from '../../ui/PrototypeBanner'
import type { Trade } from '../../../types/trade'
import { TradeNotes } from './TradeNotes'
import { TradePerformance } from './TradePerformance'
import { TradeScreenshots } from './TradeScreenshots'
import { TradeSummary } from './TradeSummary'
import { TradeTimeline } from './TradeTimeline'

interface TradeDetailsModalProps {
  trade: Trade | null
  onClose: () => void
}

/**
 * Full trade workspace: chart, contract facts, outcome, journal.
 * Composition only — each section owns its own presentation.
 */
export function TradeDetailsModal({ trade, onClose }: TradeDetailsModalProps) {
  const [confirm, setConfirm] = useState<'primary' | 'edit' | null>(null)

  if (!trade) return null

  const actionLabel =
    trade.status === 'OPEN'
      ? 'Position schließen'
      : trade.status === 'PENDING'
        ? 'Order stornieren'
        : 'Notiz hinzufügen'

  return (
    <>
      <Modal
        open
        size="xl"
        title={`${trade.symbol} · ${trade.side}`}
        subtitle={`${trade.strategy ?? 'Strategie unbekannt'} · Trade ${trade.id} · ${trade.status}`}
        onClose={onClose}
        footer={
          <>
            <Button variant="ghost" onClick={onClose}>
              Schließen
            </Button>
            <Button variant="secondary" onClick={() => setConfirm('edit')}>
              Stop anpassen
            </Button>
            <Button
              variant={trade.status === 'CLOSED' ? 'primary' : 'danger'}
              onClick={() => setConfirm('primary')}
            >
              {actionLabel}
            </Button>
          </>
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
              <TradeTimeline trade={trade} />
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <TradeNotes trade={trade} />
            <TradeScreenshots />
          </div>
        </div>
      </Modal>

      <Modal
        open={confirm !== null}
        title="Prototyp-Aktion"
        onClose={() => setConfirm(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirm(null)}>
              Abbrechen
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setConfirm(null)
                onClose()
              }}
            >
              Bestätigen (Mock)
            </Button>
          </>
        }
      >
        <PrototypeBanner>
          {confirm === 'edit'
            ? 'Stop-Anpassung ist ein UI-Platzhalter — es gibt noch keine Risiko-Engine.'
            : `„${actionLabel}" ist ein UI-Platzhalter — es gibt noch kein Order-Routing.`}
        </PrototypeBanner>
      </Modal>
    </>
  )
}
