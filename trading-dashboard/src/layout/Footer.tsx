import { useState } from 'react'
import { Mail } from 'lucide-react'
import { branding } from '../config/branding'
import { socialLinks, type SocialPlatform } from '../config/socialLinks'
import {
  TelegramIcon,
  XIcon,
  type SocialIconProps,
} from '../components/icons/SocialIcons'
import { ContactModal } from '../components/contact/ContactModal'
import { RiskDisclaimer } from '../components/ui/RiskDisclaimer'

interface SocialEntry {
  key: SocialPlatform
  label: string
  Icon: (props: SocialIconProps) => React.JSX.Element
}

const socials: SocialEntry[] = [
  { key: 'x', label: 'X (Twitter)', Icon: XIcon },
  { key: 'telegram', label: 'Telegram', Icon: TelegramIcon },
]

/**
 * Shared icon chrome. Hover lift/accent only on real hover pointers —
 * touch devices otherwise keep a sticky :hover “marked” look after tap.
 */
const iconButtonClass =
  [
    'group flex h-9 w-9 cursor-pointer items-center justify-center rounded-lg',
    'border border-[var(--color-border-subtle)] bg-[var(--color-surface)] text-[var(--color-text-muted)]',
    'transition-colors duration-200',
    'focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/45 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-bg)]',
    'active:bg-[var(--color-surface-hover)]',
    '[@media(hover:hover)_and_(pointer:fine)]:hover:-translate-y-0.5',
    '[@media(hover:hover)_and_(pointer:fine)]:hover:border-[var(--color-accent)]/50',
    '[@media(hover:hover)_and_(pointer:fine)]:hover:bg-[var(--color-accent-soft)]',
    '[@media(hover:hover)_and_(pointer:fine)]:hover:text-[var(--color-accent)]',
  ].join(' ')

/** Risk notice + social bar. Rendered at the bottom of the app shell. */
export function Footer() {
  const [contactOpen, setContactOpen] = useState(false)

  return (
    <footer className="mt-10">
      <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-6 sm:px-6 lg:px-8">
        <RiskDisclaimer />

        <div className="flex flex-col items-center justify-between gap-4 sm:flex-row">
          <p className="order-2 text-center text-xs text-[var(--color-text-muted)] sm:order-1 sm:text-left">
            © {branding.copyrightYear}{' '}
            <span className="text-[var(--color-accent)]">{branding.projectName}</span>
            {' · '}v{branding.version} · All rights reserved.
          </p>

          <nav aria-label="Social media" className="order-1 flex items-center gap-2 sm:order-2">
            {socials.map(({ key, label, Icon }) => (
              <a
                key={key}
                href={socialLinks[key]}
                aria-label={label}
                title={label}
                target={socialLinks[key] === '#' ? undefined : '_blank'}
                rel="noreferrer"
                className={iconButtonClass}
              >
                <Icon
                  size={16}
                  className="transition-transform duration-200 [@media(hover:hover)_and_(pointer:fine)]:group-hover:scale-110"
                />
              </a>
            ))}

            <button
              type="button"
              onClick={(e) => {
                setContactOpen(true)
                // Drop sticky focus/hover chrome on touch after opening.
                ;(e.currentTarget as HTMLButtonElement).blur()
              }}
              aria-label="Kontakt"
              title="Kontakt"
              className={iconButtonClass}
            >
              <Mail
                size={16}
                className="transition-transform duration-200 [@media(hover:hover)_and_(pointer:fine)]:group-hover:scale-110"
              />
            </button>
          </nav>
        </div>
      </div>

      <ContactModal open={contactOpen} onClose={() => setContactOpen(false)} />
    </footer>
  )
}
