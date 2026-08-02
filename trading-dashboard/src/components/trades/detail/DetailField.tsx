import type { ReactNode } from 'react'

interface DetailFieldProps {
  label: string
  children: ReactNode
  /** Emphasise the value (used for headline figures) */
  strong?: boolean
}

/** Label/value pair shared by the trade detail sections. */
export function DetailField({ label, children, strong = false }: DetailFieldProps) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] font-medium uppercase tracking-[0.09em] text-[var(--color-text-muted)]">
        {label}
      </dt>
      <dd
        className={[
          'mt-1 truncate tabular',
          strong ? 'text-[15px] font-semibold' : 'text-sm',
          'text-[var(--color-text)]',
        ].join(' ')}
      >
        {children}
      </dd>
    </div>
  )
}

interface DetailCardProps {
  title: string
  children: ReactNode
  actions?: ReactNode
}

/** Panel wrapper giving every detail section the same rhythm. */
export function DetailCard({ title, children, actions }: DetailCardProps) {
  return (
    <section className="panel p-4 sm:p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-secondary)]">
          {title}
        </h3>
        {actions}
      </div>
      {children}
    </section>
  )
}
