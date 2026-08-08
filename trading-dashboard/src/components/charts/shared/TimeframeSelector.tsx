import {
  TIMEFRAMES,
  TIMEFRAME_LABELS,
  type CandleInterval,
} from '../../../services/marketData'

interface TimeframeSelectorProps {
  value: CandleInterval
  onChange: (interval: CandleInterval) => void
  disabled?: boolean
}

/** Segmented timeframe bar in the style of professional charting tools. */
export function TimeframeSelector({ value, onChange, disabled = false }: TimeframeSelectorProps) {
  return (
    <div
      role="tablist"
      aria-label="Timeframe"
      className="flex flex-wrap items-center gap-0.5 rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-surface)] p-0.5"
    >
      {TIMEFRAMES.map((tf) => {
        const active = tf === value
        return (
          <button
            key={tf}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={disabled}
            onClick={() => onChange(tf)}
            className={[
              'min-w-[34px] cursor-pointer rounded-md px-2 py-1 text-[11px] font-medium tabular transition-all duration-150',
              active
                ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)] shadow-[inset_0_0_0_1px_var(--color-accent)]'
                : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]',
              disabled ? 'cursor-not-allowed opacity-50' : '',
            ].join(' ')}
          >
            {TIMEFRAME_LABELS[tf]}
          </button>
        )
      })}
    </div>
  )
}
