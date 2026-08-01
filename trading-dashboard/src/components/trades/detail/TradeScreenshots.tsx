import { ImagePlus } from 'lucide-react'
import { DetailCard } from './DetailField'

const slots = ['Vor dem Trade', 'Nach dem Trade'] as const

/** Upload placeholder for future before/after chart captures. */
export function TradeScreenshots() {
  return (
    <DetailCard title="Screenshots">
      <div className="grid gap-3 sm:grid-cols-2">
        {slots.map((slot) => (
          <div
            key={slot}
            className="flex min-h-[132px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-surface)]/50 px-4 py-6 text-center"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]">
              <ImagePlus size={16} strokeWidth={1.8} />
            </span>
            <p className="text-xs font-medium text-[var(--color-text-secondary)]">{slot}</p>
            <p className="text-[11px] text-[var(--color-text-muted)]">
              Bild ablegen oder auswählen
            </p>
          </div>
        ))}
      </div>
    </DetailCard>
  )
}
