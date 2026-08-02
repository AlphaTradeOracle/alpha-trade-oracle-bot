import { branding } from '../../config/branding'
import { Logo } from './Logo'

interface BrandLockupProps {
  size?: number
  /** Hide the text block (e.g. tight mobile headers) */
  compact?: boolean
}

/** Logo + wordmark used in the sidebar and mobile header. */
export function BrandLockup({ size = 44, compact = false }: BrandLockupProps) {
  return (
    <div className="flex items-center gap-2.5">
      <Logo size={size} />
      {compact ? null : (
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold tracking-tight text-[var(--color-text)]">
            {branding.projectName}
          </p>
          <p className="truncate text-[11px] text-[var(--color-text-muted)]">
            {branding.tagline}
          </p>
        </div>
      )}
    </div>
  )
}
