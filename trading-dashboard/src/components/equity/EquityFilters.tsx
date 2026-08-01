import { useState } from 'react'
import { CalendarRange } from 'lucide-react'

export type EquityRangeId = 'all' | 'ytd' | '12m' | '90d' | '30d' | 'custom'

export interface EquityRange {
  id: EquityRangeId
  /** Unix seconds; null means "everything loaded" */
  from: number | null
  to: number | null
}

interface EquityFiltersProps {
  value: EquityRangeId
  onChange: (range: EquityRange) => void
}

const presets: { id: EquityRangeId; label: string }[] = [
  { id: 'all', label: 'Alle Daten' },
  { id: 'ytd', label: 'Dieses Jahr' },
  { id: '12m', label: 'Letzte 12 Monate' },
  { id: '90d', label: 'Letzte 90 Tage' },
  { id: '30d', label: 'Letzte 30 Tage' },
]

/** Resolves a preset into an absolute window. */
export function resolveRange(id: EquityRangeId): EquityRange {
  const now = Math.floor(Date.now() / 1000)
  switch (id) {
    case 'ytd': {
      const jan1 = Math.floor(new Date(new Date().getFullYear(), 0, 1).getTime() / 1000)
      return { id, from: jan1, to: now }
    }
    case '12m':
      return { id, from: now - 365 * 86_400, to: now }
    case '90d':
      return { id, from: now - 90 * 86_400, to: now }
    case '30d':
      return { id, from: now - 30 * 86_400, to: now }
    default:
      return { id: 'all', from: null, to: null }
  }
}

/** Range presets plus a custom window for the equity analysis. */
export function EquityFilters({ value, onChange }: EquityFiltersProps) {
  const [customOpen, setCustomOpen] = useState(false)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  const applyCustom = () => {
    if (!from || !to) return
    onChange({
      id: 'custom',
      from: Math.floor(new Date(from).getTime() / 1000),
      to: Math.floor(new Date(to).getTime() / 1000),
    })
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {presets.map((preset) => {
        const active = preset.id === value
        return (
          <button
            key={preset.id}
            type="button"
            onClick={() => {
              setCustomOpen(false)
              onChange(resolveRange(preset.id))
            }}
            className={[
              'cursor-pointer rounded-md px-2 py-1 text-[11px] transition-colors duration-150',
              active
                ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]',
            ].join(' ')}
          >
            {preset.label}
          </button>
        )
      })}

      <button
        type="button"
        onClick={() => setCustomOpen((v) => !v)}
        className={[
          'flex cursor-pointer items-center gap-1 rounded-md px-2 py-1 text-[11px] transition-colors duration-150',
          value === 'custom'
            ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
            : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]',
        ].join(' ')}
      >
        <CalendarRange size={12} />
        Eigener Zeitraum
      </button>

      {customOpen ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <input
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[11px] text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]"
          />
          <span className="text-[11px] text-[var(--color-text-muted)]">–</span>
          <input
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[11px] text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]"
          />
          <button
            type="button"
            onClick={applyCustom}
            className="cursor-pointer rounded-md bg-[var(--color-accent)] px-2 py-1 text-[11px] font-medium text-[#061018] transition hover:brightness-110"
          >
            Anwenden
          </button>
        </div>
      ) : null}
    </div>
  )
}
