import { branding } from '../../config/branding'

interface LogoProps {
  /** Rendered edge length in px — the image scales proportionally. */
  size?: number
  className?: string
}

/**
 * Brand mark — SVG primary so edges stay sharp at sidebar sizes.
 */
export function Logo({ size = 44, className = '' }: LogoProps) {
  return (
    <img
      src={branding.logoSrc}
      alt={`${branding.projectName} logo`}
      width={size}
      height={size}
      loading="eager"
      decoding="async"
      draggable={false}
      className={['shrink-0 object-contain', className].join(' ')}
      style={{
        width: size,
        height: size,
        // Prefer crisp vector scaling; avoid soft browser resampling.
        imageRendering: 'auto',
      }}
    />
  )
}
