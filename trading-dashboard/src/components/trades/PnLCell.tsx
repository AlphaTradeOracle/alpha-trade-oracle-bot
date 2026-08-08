import { formatPct, formatSignedMoney } from '../../utils/format'

interface PnLCellProps {
  value: number | null | undefined
  /** When true, format as signed percent instead of money */
  asPct?: boolean
}

export function PnLCell({ value, asPct = false }: PnLCellProps) {
  if (value == null || Number.isNaN(value)) {
    return <span className="tabular text-[var(--color-text-muted)]">—</span>
  }

  const positive = value > 0
  const negative = value < 0
  const color = positive
    ? 'text-[var(--color-long)]'
    : negative
      ? 'text-[var(--color-short)]'
      : 'text-[var(--color-text-secondary)]'

  return (
    <span className={`tabular font-medium ${color}`}>
      {asPct ? formatPct(value) : formatSignedMoney(value)}
    </span>
  )
}
