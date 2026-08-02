import { branding } from '../../config/branding'
import { Logo } from './Logo'

interface BrandLockupProps {
  size?: number
  /** Hide the text block (e.g. tight mobile headers) */
  compact?: boolean
  /** Horizontal lockup for narrow bars; default is stacked (logo above wordmark). */
  stacked?: boolean
}

/** Logo + wordmark used in the sidebar and mobile header. */
export function BrandLockup({
  size = 96,
  compact = false,
  stacked = true,
}: BrandLockupProps) {
  if (compact) {
    return <Logo size={size} />
  }

  if (!stacked) {
    return (
      <div className="flex items-center gap-2.5">
        <Logo size={size} />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold tracking-tight text-[var(--color-text)]">
            {branding.projectName}
          </p>
          <p className="truncate text-[11px] text-[var(--color-text-muted)]">{branding.tagline}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center gap-3 text-center">
      <Logo size={size} className="drop-shadow-[0_6px_18px_color-mix(in_srgb,var(--color-accent)_28%,transparent)]" />
      <div className="min-w-0 space-y-1">
        <p className="text-base font-semibold leading-tight tracking-tight text-[var(--color-text)]">
          {branding.projectName}
        </p>
        <p className="text-xs font-medium uppercase tracking-[0.14em] text-[var(--color-text-muted)]">
          {branding.tagline}
        </p>
      </div>
    </div>
  )
}
