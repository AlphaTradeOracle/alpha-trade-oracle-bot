import { EQUITY_OVERLAYS, type EquityOverlayId } from './EquityOverlays'

interface EquityOverlayMenuProps {
  active: EquityOverlayId[]
  onToggle: (id: EquityOverlayId) => void
}

/** Chip row for switching metric overlays on and off. */
export function EquityOverlayMenu({ active, onToggle }: EquityOverlayMenuProps) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {EQUITY_OVERLAYS.map((overlay) => {
        const on = active.includes(overlay.id)
        return (
          <button
            key={overlay.id}
            type="button"
            disabled={!overlay.available}
            onClick={() => onToggle(overlay.id)}
            aria-pressed={on}
            className={[
              'flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] transition-all duration-150',
              on
                ? 'border-[var(--color-accent)]/50 bg-[var(--color-accent-soft)] text-[var(--color-text)]'
                : 'border-[var(--color-border-subtle)] bg-[var(--color-surface)] text-[var(--color-text-muted)]',
              overlay.available
                ? 'cursor-pointer hover:border-[var(--color-accent)]/40 hover:text-[var(--color-text)]'
                : 'cursor-not-allowed opacity-45',
            ].join(' ')}
          >
            <span
              className="h-1.5 w-1.5 rounded-full"
              style={{ background: on ? overlay.color : 'currentColor' }}
            />
            {overlay.label}
          </button>
        )
      })}
    </div>
  )
}
