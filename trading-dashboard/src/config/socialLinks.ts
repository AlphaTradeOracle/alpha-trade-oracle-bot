/**
 * Central social link configuration.
 * Replace the placeholders with real profile URLs — the footer picks them up.
 */
export const socialLinks = {
  x: '#',
  telegram: '#',
  youtube: '#',
  instagram: '#',
} as const

export type SocialPlatform = keyof typeof socialLinks
