import { useState } from 'react'
import type { Trade } from '../../types/trade'
import { formatDateTime, formatMoney, formatPrice } from '../../utils/format'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'
import { PrototypeBanner } from '../ui/PrototypeBanner'
import { PnLCell } from './PnLCell'
import { ScoreBadge } from './ScoreBadge'
import { SideBadge } from './SideBadge'

interface TradeDetailModalProps {
  trade: Trade | null
  onClose: () => void
}

/**
 * Prototype trade inspector.
 * Action buttons open nested dummy confirmations — no real mutations yet.
 */
export function TradeDetailModal({ trade, onClose }: TradeDetailModalProps) {
  const [confirm, setConfirm] = useState<string | null>(null)

  if (!trade) return null

  const actionLabel =
    trade.status === 'OPEN'
      ? 'Close position'
      : trade.status === 'PENDING'
        ? 'Cancel order'
        : 'Add journal note'

  return (
    <>
      <Modal
        open
        title={`${trade.symbol} · ${trade.status}`}
        onClose={onClose}
        size="lg"
        footer={
          <>
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
            <Button variant="secondary" onClick={() => setConfirm('edit')}>
              Edit stop
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
          <PrototypeBanner>
            Mock detail view — actions do not change the book yet.
          </PrototypeBanner>

          <div className="flex flex-wrap items-center gap-2">
            <SideBadge side={trade.side} />
            <ScoreBadge score={trade.score} />
            <span className="text-xs text-[var(--color-text-muted)]">ID {trade.id}</span>
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
            <Field label="Entry" value={formatPrice(trade.entry)} />
            <Field label="Mark" value={formatPrice(trade.mark)} />
            <Field label="Stop" value={formatPrice(trade.stop)} />
            <Field label="Exit" value={formatPrice(trade.exit)} />
            <Field label="Margin" value={formatMoney(trade.margin)} />
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">
                uPnL / Realized
              </dt>
              <dd className="mt-1 flex gap-3">
                <PnLCell value={trade.upnl} />
                <PnLCell value={trade.realized} />
              </dd>
            </div>
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">R</dt>
              <dd className="mt-1">
                <PnLCell value={trade.r} asR />
              </dd>
            </div>
            <Field label="Opened" value={formatDateTime(trade.openedAt)} />
            <Field label="Closed" value={formatDateTime(trade.closedAt)} />
          </dl>
        </div>
      </Modal>

      <Modal
        open={confirm !== null}
        title="Prototype action"
        onClose={() => setConfirm(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setConfirm(null)
                onClose()
              }}
            >
              Confirm (mock)
            </Button>
          </>
        }
      >
        <PrototypeBanner>
          {confirm === 'edit'
            ? 'Stop edit is a UI placeholder — no risk engine yet.'
            : `${actionLabel} is a UI placeholder — no order routing yet.`}
        </PrototypeBanner>
      </Modal>
    </>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">{label}</dt>
      <dd className="mt-1 tabular text-[var(--color-text)]">{value}</dd>
    </div>
  )
}
