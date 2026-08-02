import { branding } from '../../config/branding'
import { Logo } from './Logo'

interface BrandLockupProps {
  size?: number
  /** Hide the text block (e.g. tight mobile headers) */
  compact?: boolean
  /** Horizontal lockup for narrow bars; default is stacked (logo above tagline). */
  stacked?: boolean
}

/**
 * Brand mark for sidebar / mobile header.
 * The PNG already includes the wordmark — do not duplicate "Alpha Trade Oracle".
 */
export function BrandLockup({
  size = 80,
  compact = false,
  stacked = true,
}: BrandLockupProps) {
  if (compact) {
    return <Logo size={size} />
  }

  if (!stacked) {
    return (
      <div className="flex min-w-0 items-center gap-2.5">
        <Logo size={size} />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold tracking-tight text-[var(--color-text)]">
            {branding.shortName}
          </p>
          <p className="truncate text-[11px] text-[var(--color-text-muted)]">{branding.tagline}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex w-full flex-col items-center gap-2 text-center">
      <Logo
        size={size}
        className="drop-shadow-[0_6px_18px_color-mix(in_srgb,var(--color-accent)_28%,transparent)]"
      />
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-text-muted)]">
        {branding.tagline}
      </p>
    </div>
  )
}
