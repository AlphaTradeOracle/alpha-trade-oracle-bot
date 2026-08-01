/**
 * Central brand configuration.
 *
 * To swap the logo, drop your file into `public/brand/` and point
 * `logoSrc` at it — no component changes required.
 * Recommended: square PNG/SVG, at least 512×512, transparent or dark background.
 */
export const branding = {
  projectName: 'Alpha Trade Oracle',
  shortName: 'Alpha Desk',
  tagline: 'Local trading console',
  version: '0.1 Alpha',
  copyrightYear: 2026,
  /** Primary logo file served from `public/`. */
  logoSrc: '/brand/logo.png',
  /** Rendered when the logo file is missing (keeps the shell intact). */
  logoFallbackSrc: '/brand/logo-fallback.svg',
} as const

export type Branding = typeof branding
