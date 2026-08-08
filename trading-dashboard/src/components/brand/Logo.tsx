import { branding } from '../../config/branding'

interface LogoProps {
  /** Rendered edge length in px — square source, scaled with object-contain. */
  size?: number
  className?: string
}

/**
 * Brand lockup — high-res PNG (same asset as Telegram / charts).
 * Always preserves 1:1 aspect ratio; never stretches.
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
      className={['block shrink-0 select-none object-contain object-center', className]
        .filter(Boolean)
        .join(' ')}
      style={{
        width: size,
        height: size,
        maxWidth: '100%',
        aspectRatio: '1 / 1',
        imageRendering: 'auto',
      }}
    />
  )
}
