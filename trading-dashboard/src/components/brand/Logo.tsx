import { useState } from 'react'
import { branding } from '../../config/branding'

interface LogoProps {
  /** Rendered edge length in px — the image scales proportionally. */
  size?: number
  className?: string
}

/**
 * Brand mark.
 * Loads `branding.logoSrc` and falls back to the bundled SVG when the
 * file has not been dropped in yet, so the shell never breaks.
 */
export function Logo({ size = 40, className = '' }: LogoProps) {
  const [src, setSrc] = useState<string>(branding.logoSrc)

  return (
    <img
      src={src}
      onError={() => setSrc(branding.logoFallbackSrc)}
      alt={`${branding.projectName} logo`}
      width={size}
      height={size}
      loading="eager"
      decoding="async"
      className={[
        'shrink-0 rounded-lg object-contain',
        className,
      ].join(' ')}
      style={{ width: size, height: size }}
    />
  )
}
