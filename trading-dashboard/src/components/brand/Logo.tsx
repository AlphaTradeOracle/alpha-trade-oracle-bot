import { branding } from '../../config/branding'

interface LogoProps {
  /** Rendered edge length in px — the image scales proportionally. */
  size?: number
  className?: string
}

/**
 * Brand mark — SVG primary so edges stay sharp at any size.
 */
export function Logo({ size = 80, className = '' }: LogoProps) {
  return (
    <img
      src={branding.logoSrc}
      alt={`${branding.projectName} logo`}
      width={size}
      height={size}
      loading="eager"
      decoding="sync"
      fetchPriority="high"
      draggable={false}
      className={['shrink-0 select-none object-contain', className].join(' ')}
      style={{
        width: size,
        height: size,
        imageRendering: 'auto',
      }}
    />
  )
}
