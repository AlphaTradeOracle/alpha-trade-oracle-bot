import { useState } from 'react'
import { Download, Plus, RefreshCw } from 'lucide-react'
import { Button } from './Button'
import { Modal } from './Modal'
import { PrototypeBanner } from './PrototypeBanner'

interface PrototypeActionsProps {
  /** Context label shown inside dummy dialogs */
  context: string
  showNewTrade?: boolean
}

/**
 * Common header actions for trade pages.
 * Every button is clickable and opens a mock dialog.
 */
export function PrototypeActions({
  context,
  showNewTrade = true,
}: PrototypeActionsProps) {
  const [dialog, setDialog] = useState<'refresh' | 'export' | 'new' | null>(null)

  const copy =
    dialog === 'refresh'
      ? `Refresh “${context}” would reload mock / live marks. Not wired yet.`
      : dialog === 'export'
        ? `Export “${context}” would download CSV/JSON. Not wired yet.`
        : `New trade for “${context}” would open an order ticket. Not wired yet.`

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" onClick={() => setDialog('refresh')}>
          <RefreshCw size={14} />
          Refresh
        </Button>
        <Button variant="secondary" onClick={() => setDialog('export')}>
          <Download size={14} />
          Export
        </Button>
        {showNewTrade ? (
          <Button variant="primary" onClick={() => setDialog('new')}>
            <Plus size={14} />
            New trade
          </Button>
        ) : null}
      </div>

      <Modal
        open={dialog !== null}
        title="Prototype action"
        onClose={() => setDialog(null)}
        footer={
          <Button variant="primary" onClick={() => setDialog(null)}>
            Got it
          </Button>
        }
      >
        <PrototypeBanner>{copy}</PrototypeBanner>
      </Modal>
    </>
  )
}
