import type { LucideIcon } from 'lucide-react'
import { formatPct } from '../../utils/format'

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
}: KpiCardProps) {
  const className = [
    'panel flex min-h-[108px] w-full flex-col justify-between gap-3 p-4 text-left transition-colors hover:bg-[var(--color-surface-hover)]',
    onClick ? 'cursor-pointer' : '',
  ].join(' ')

  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
          {title}
        </p>
        <span className={`rounded-lg p-1.5 ${toneIcon[tone]}`}>
          <Icon size={15} strokeWidth={1.8} />
        </span>
      </div>
      <div>
        <p className={`tabular text-xl font-semibold tracking-tight sm:text-[1.35rem] ${toneValue[tone]}`}>
          {value}
        </p>
        {hint ? (
          <p className="mt-1 text-xs tabular text-[var(--color-text-muted)]">{hint}</p>
        ) : null}
        {deltaPct != null ? (
          <p
            className={[
              'mt-1 text-xs tabular',
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
      </div>
    </>
  )

  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={className}>
        {body}
      </button>
    )
  }

  return <article className={className}>{body}</article>
}
