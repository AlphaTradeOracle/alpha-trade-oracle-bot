import { useState } from 'react'
import type { Trade } from '../../../types/trade'
import { DetailCard } from './DetailField'

interface TradeNotesProps {
  trade: Trade
}

/**
 * Journal placeholder.
 * Text lives in local state only — persistence arrives with the journal feature.
 */
export function TradeNotes({ trade }: TradeNotesProps) {
  const [value, setValue] = useState(trade.notes ?? '')

  return (
    <DetailCard
      title="Notes"
      actions={
        <span className="text-[10px] text-[var(--color-text-muted)]">
          wird noch nicht gespeichert
        </span>
      }
    >
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        rows={5}
        placeholder="Setup, Ausführung, Fehler, Learnings …"
        className="w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2.5 text-sm leading-relaxed text-[var(--color-text)] outline-none transition-colors placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent)]"
      />
    </DetailCard>
  )
}
