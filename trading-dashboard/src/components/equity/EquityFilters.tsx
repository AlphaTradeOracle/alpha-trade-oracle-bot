import type { CandleInterval } from '../../services/marketData'

/** Periods offered for the equity analysis. */
export type EquityRangeId = '30d' | '90d' | '12m' | 'all'

export interface EquityRangeDefinition {
  id: EquityRangeId
  label: string
  /** Window length in seconds; null means "since account opening" */
  seconds: number | null
  /** Sampling resolution that keeps the point count readable */
  interval: CandleInterval
}

export const EQUITY_RANGES: EquityRangeDefinition[] = [
  { id: '30d', label: '30 Tage', seconds: 30 * 86_400, interval: '1h' },
  { id: '90d', label: '90 Tage', seconds: 90 * 86_400, interval: '4h' },
  { id: '12m', label: 'Letzte 12 Monate', seconds: 365 * 86_400, interval: '1d' },
  { id: 'all', label: 'All', seconds: null, interval: '1d' },
]

export const DEFAULT_EQUITY_RANGE: EquityRangeId = '90d'

export function getRange(id: EquityRangeId): EquityRangeDefinition {
  return EQUITY_RANGES.find((r) => r.id === id) ?? EQUITY_RANGES[1]
}

interface EquityFiltersProps {
  value: EquityRangeId
  onChange: (id: EquityRangeId) => void
  disabled?: boolean
}

/** Segmented period selector above the equity chart. */
export function EquityFilters({ value, onChange, disabled = false }: EquityFiltersProps) {
  return (
    <div
      role="tablist"
      aria-label="Zeitraum"
      className="flex flex-wrap items-center gap-0.5 rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-surface)] p-0.5"
    >
      {EQUITY_RANGES.map((range) => {
        const active = range.id === value
        return (
          <button
            key={range.id}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={disabled}
            onClick={() => onChange(range.id)}
            className={[
              'cursor-pointer rounded-md px-2.5 py-1 text-[11px] font-medium transition-all duration-150',
              active
                ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)] shadow-[inset_0_0_0_1px_var(--color-accent)]'
                : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]',
              disabled ? 'cursor-not-allowed opacity-50' : '',
            ].join(' ')}
          >
            {range.label}
          </button>
        )
      })}
    </div>
  )
}
