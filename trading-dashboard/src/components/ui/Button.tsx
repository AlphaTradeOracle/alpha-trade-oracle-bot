import type { ButtonHTMLAttributes, ReactNode } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  children: ReactNode
}

const styles: Record<Variant, string> = {
  primary:
    'bg-[var(--color-accent)] text-[#061018] hover:brightness-110 disabled:opacity-50',
  secondary:
    'border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]',
  ghost:
    'text-[var(--color-text-secondary)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text)]',
  danger:
    'border border-[var(--color-short)]/40 bg-[var(--color-short-soft)] text-[var(--color-short)] hover:brightness-110',
}

/** Shared button styling used across the desk. */
export function Button({
  variant = 'secondary',
  className = '',
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={[
        'inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition',
        styles[variant],
        className,
      ].join(' ')}
      {...rest}
    >
      {children}
    </button>
  )
}
