import type { LucideIcon } from 'lucide-react'
import { formatPct } from '../../utils/format'
import { Tooltip } from '../ui/Tooltip'

export type KpiTone = 'neutral' | 'positive' | 'negative' | 'accent'

interface KpiCardProps {
  title: string
  value: string
  icon: LucideIcon
  deltaPct?: number | null
  /** Secondary line under the value (e.g. "WR 55%"). */
  hint?: string | null
  tone?: KpiTone
  onClick?: () => void
  /** Hover / focus / tap help text from central KPI_TOOLTIPS config. */
  tooltip?: string
}

const toneValue: Record<KpiTone, string> = {
  neutral: 'text-[var(--color-text)]',
  positive: 'text-[var(--color-long)]',
  negative: 'text-[var(--color-short)]',
  accent: 'text-[var(--color-accent)]',
}

const toneIcon: Record<KpiTone, string> = {
  neutral: 'bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]',
  positive: 'bg-[var(--color-long-soft)] text-[var(--color-long)]',
  negative: 'bg-[var(--color-short-soft)] text-[var(--color-short)]',
  accent: 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]',
}

export function KpiCard({
  title,
  value,
  icon: Icon,
  deltaPct,
  hint,
  tone = 'neutral',
  onClick,
  tooltip,
}: KpiCardProps) {
  const className = [
    'panel relative flex min-h-[108px] w-full flex-col items-center justify-center gap-2 px-4 pb-3.5 pt-4 text-center transition-colors hover:bg-[var(--color-surface-hover)]',
    onClick ? 'cursor-pointer' : '',
  ].join(' ')

  const body = (
    <>
      <span
        className={`absolute right-3 top-3 rounded-lg p-1.5 ${toneIcon[tone]}`}
        aria-hidden
      >
        <Icon size={15} strokeWidth={1.8} />
      </span>
      <p className="w-full px-6 text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
        {title}
      </p>
      <p
        className={`tabular w-full text-xl font-semibold leading-none tracking-tight sm:text-[1.35rem] ${toneValue[tone]}`}
      >
        {value}
      </p>
      {hint ? (
        <p className="tabular text-xs text-[var(--color-text-muted)]">{hint}</p>
      ) : null}
      {deltaPct != null ? (
        <p
          className={[
            'tabular text-xs',
            deltaPct > 0
              ? 'text-[var(--color-long)]'
              : deltaPct < 0
                ? 'text-[var(--color-short)]'
                : 'text-[var(--color-text-muted)]',
          ].join(' ')}
        >
          {formatPct(deltaPct)} vs prior
        </p>
      ) : null}
    </>
  )

  const card = onClick ? (
    <button type="button" onClick={onClick} className={className}>
      {body}
    </button>
  ) : (
    <article className={className}>{body}</article>
  )

  if (!tooltip) return card
  return (
    <Tooltip content={tooltip} keyboardFocus={!onClick}>
      {card}
    </Tooltip>
  )
}
