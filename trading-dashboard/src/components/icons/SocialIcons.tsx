import type { SVGProps } from 'react'

/**
 * Brand glyphs for social platforms.
 * Lucide 1.x dropped brand marks, so these are kept local and stroke/fill
 * consistent with the rest of the icon set.
 */
export type SocialIconProps = SVGProps<SVGSVGElement> & { size?: number }

function base({ size = 18, ...rest }: SocialIconProps) {
  return {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    'aria-hidden': true,
    focusable: false,
    ...rest,
  }
}

export function XIcon(props: SocialIconProps) {
  return (
    <svg {...base(props)} fill="currentColor">
      <path d="M17.53 3h3.02l-6.6 7.54L21.75 21h-6.07l-4.76-6.22L5.47 21H2.44l7.06-8.07L2.25 3h6.22l4.3 5.69L17.53 3Zm-1.06 16.17h1.67L7.6 4.73H5.81l10.66 14.44Z" />
    </svg>
  )
}

export function TelegramIcon(props: SocialIconProps) {
  return (
    <svg {...base(props)} fill="currentColor">
      <path d="M21.72 4.28a1.5 1.5 0 0 0-1.55-.24L3.4 10.66c-1.2.47-1.16 2.19.06 2.6l4.1 1.38 1.6 4.94c.31.95 1.53 1.2 2.19.45l2.2-2.5 4.2 3.1c.79.58 1.92.16 2.13-.8l2.06-13.5a1.5 1.5 0 0 0-.22-1.05ZM9.6 13.9l8.3-5.1-6.6 6.06a1.5 1.5 0 0 0-.47.93l-.24 1.9-.99-3.79Z" />
    </svg>
  )
}
