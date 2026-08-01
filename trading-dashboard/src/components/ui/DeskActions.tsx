import { useState } from 'react'
import { Download, Plus, RefreshCw } from 'lucide-react'
import { Button } from './Button'
import { Modal } from './Modal'

interface DeskActionsProps {
  /** Section the actions belong to, shown inside the dialogs */
  context: string
  showNewTrade?: boolean
}

type Dialog = 'refresh' | 'export' | 'new' | null

const exportFormats = ['CSV', 'JSON', 'Excel'] as const

/** Standard header actions for the desk views. */
export function DeskActions({ context, showNewTrade = true }: DeskActionsProps) {
  const [dialog, setDialog] = useState<Dialog>(null)
  const [format, setFormat] = useState<(typeof exportFormats)[number]>('CSV')

  const close = () => setDialog(null)

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" onClick={() => setDialog('refresh')}>
          <RefreshCw size={14} />
          Aktualisieren
        </Button>
        <Button variant="secondary" onClick={() => setDialog('export')}>
          <Download size={14} />
          Export
        </Button>
        {showNewTrade ? (
          <Button variant="primary" onClick={() => setDialog('new')}>
            <Plus size={14} />
            Neue Position
          </Button>
        ) : null}
      </div>

      <Modal
        open={dialog === 'refresh'}
        title="Daten aktualisieren"
        subtitle={`Aktualisiert Kurse und Kennzahlen für „${context}".`}
        onClose={close}
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Abbrechen
            </Button>
            <Button variant="primary" onClick={close}>
              Jetzt aktualisieren
            </Button>
          </>
        }
      >
        <p>
          Die Marktdaten werden neu abgefragt und alle Kennzahlen der Ansicht neu berechnet.
        </p>
      </Modal>

      <Modal
        open={dialog === 'export'}
        title="Export"
        subtitle={`Exportiert die aktuell gefilterten Daten aus „${context}".`}
        onClose={close}
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Abbrechen
            </Button>
            <Button variant="primary" onClick={close}>
              Export starten
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-xs text-[var(--color-text-muted)]">Format wählen</p>
          <div className="flex flex-wrap gap-2">
            {exportFormats.map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFormat(f)}
                className={[
                  'cursor-pointer rounded-lg border px-3 py-1.5 text-sm transition-colors',
                  f === format
                    ? 'border-[var(--color-accent)]/60 bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                    : 'border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]',
                ].join(' ')}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
      </Modal>

      <Modal
        open={dialog === 'new'}
        title="Neue Position"
        subtitle="Order-Ticket für den manuellen Einstieg."
        onClose={close}
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Abbrechen
            </Button>
            <Button variant="primary" onClick={close}>
              Order anlegen
            </Button>
          </>
        }
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Symbol" placeholder="BTCUSDT" />
          <div className="space-y-1.5">
            <span className="block text-sm text-[var(--color-text-secondary)]">Richtung</span>
            <select className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2.5 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]">
              <option>Long</option>
              <option>Short</option>
            </select>
          </div>
          <Field label="Einstieg" placeholder="0.00" />
          <Field label="Stop Loss" placeholder="0.00" />
          <Field label="Größe" placeholder="0.00" />
          <Field label="Hebel" placeholder="5" />
        </div>
      </Modal>
    </>
  )
}

function Field({ label, placeholder }: { label: string; placeholder: string }) {
  return (
    <label className="block space-y-1.5">
      <span className="block text-sm text-[var(--color-text-secondary)]">{label}</span>
      <input
        type="text"
        placeholder={placeholder}
        className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2.5 text-sm text-[var(--color-text)] outline-none transition-colors placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent)]"
      />
    </label>
  )
}
