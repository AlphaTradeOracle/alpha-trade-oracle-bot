import { useState } from 'react'
import { Mail } from 'lucide-react'
import { branding } from '../config/branding'
import { socialLinks, type SocialPlatform } from '../config/socialLinks'
import {
  InstagramIcon,
  TelegramIcon,
  XIcon,
  YoutubeIcon,
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
  { key: 'youtube', label: 'YouTube', Icon: YoutubeIcon },
  { key: 'instagram', label: 'Instagram', Icon: InstagramIcon },
]

/** Shared styling so the contact button matches the social icons exactly. */
const iconButtonClass =
  'group flex h-9 w-9 cursor-pointer items-center justify-center rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-surface)] text-[var(--color-text-muted)] transition-all duration-200 hover:-translate-y-0.5 hover:border-[var(--color-accent)]/50 hover:bg-[var(--color-accent-soft)] hover:text-[var(--color-accent)]'

/** Risk notice + social bar. Rendered at the bottom of the app shell. */
export function Footer() {
  const [contactOpen, setContactOpen] = useState(false)

  return (
    <footer className="mt-10 border-t border-[var(--color-border-subtle)] bg-[var(--color-bg-elevated)]/60">
      <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-6 sm:px-6 lg:px-8">
        <RiskDisclaimer />

        <div className="flex flex-col items-center justify-between gap-4 sm:flex-row">
          <p className="order-2 text-center text-xs text-[var(--color-text-muted)] sm:order-1 sm:text-left">
            © {branding.copyrightYear}
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
                <Icon size={16} className="transition-transform duration-200 group-hover:scale-110" />
              </a>
            ))}

            <button
              type="button"
              onClick={() => setContactOpen(true)}
              aria-label="Kontakt"
              title="Kontakt"
              className={iconButtonClass}
            >
              <Mail size={16} className="transition-transform duration-200 group-hover:scale-110" />
            </button>
          </nav>
        </div>
      </div>

      <ContactModal open={contactOpen} onClose={() => setContactOpen(false)} />
    </footer>
  )
}
