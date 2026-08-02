/**
 * Central brand configuration.
 *
 * Primary mark is the same crystal-clear PNG used for Telegram / chart overlays.
 * Drop a replacement into `public/brand/` and point `logoSrc` at it.
 */
export const branding = {
  projectName: 'Alpha Trade Oracle',
  shortName: 'Alpha Desk',
  tagline: 'Live Dashboard',
  version: '0.1 Alpha',
  copyrightYear: 2026,
  /** Full lockup (icon + wordmark) — matches Telegram / chart asset. */
  logoSrc: '/brand/logo.png',
  /** Optional alternate mark path. */
  logoFallbackSrc: '/brand/logo.png',
} as const

export type Branding = typeof branding
