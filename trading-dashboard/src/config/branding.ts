/**
 * Central brand configuration.
 *
 * To swap the logo, drop your file into `public/brand/` and point
 * `logoSrc` at it — no component changes required.
 * Recommended: square SVG mark (transparent). PNG only as fallback asset.
 */
export const branding = {
  projectName: 'Alpha Trade Oracle',
  shortName: 'Alpha Desk',
  tagline: 'Live Dashboard',
  version: '0.1 Alpha',
  copyrightYear: 2026,
  /** Primary logo — SVG stays sharp at small sidebar sizes. */
  logoSrc: '/brand/logo.svg',
  /** Legacy raster mark (optional). */
  logoFallbackSrc: '/brand/logo.png',
} as const

export type Branding = typeof branding
