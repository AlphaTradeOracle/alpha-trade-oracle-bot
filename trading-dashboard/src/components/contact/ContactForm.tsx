import { useId, useState } from 'react'
import {
  CONTACT_MESSAGE_MAX,
  type ContactFormErrors,
  type ContactMessage,
} from '../../types/contact'
import { CharacterCounter } from './CharacterCounter'

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/

export function validateContact(values: {
  email: string
  message: string
}): ContactFormErrors {
  const errors: ContactFormErrors = {}

  if (!values.email.trim()) {
    errors.email = 'Bitte gib eine E-Mail-Adresse an.'
  } else if (!EMAIL_PATTERN.test(values.email.trim())) {
    errors.email = 'Diese E-Mail-Adresse sieht nicht gültig aus.'
  }

  if (!values.message.trim()) {
    errors.message = 'Bitte beschreibe dein Anliegen.'
  } else if (values.message.length > CONTACT_MESSAGE_MAX) {
    errors.message = `Maximal ${CONTACT_MESSAGE_MAX.toLocaleString('de-DE')} Zeichen.`
  }

  return errors
}

interface ContactFormProps {
  /** Called with a valid payload; the parent handles delivery. */
  onSubmit: (message: ContactMessage) => void
  disabled?: boolean
  /** Wire the external submit button to this form element. */
  formId: string
}

const fieldClass =
  'w-full rounded-lg border bg-[var(--color-surface)] px-3 py-2.5 text-sm text-[var(--color-text)] outline-none transition-colors placeholder:text-[var(--color-text-muted)]'

export function ContactForm({ onSubmit, disabled = false, formId }: ContactFormProps) {
  const emailId = useId()
  const telegramId = useId()
  const messageId = useId()

  const [email, setEmail] = useState('')
  const [telegram, setTelegram] = useState('')
  const [message, setMessage] = useState('')
  const [errors, setErrors] = useState<ContactFormErrors>({})

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const found = validateContact({ email, message })
    setErrors(found)
    if (Object.keys(found).length > 0) return

    onSubmit({
      email: email.trim(),
      telegram: telegram.trim() || undefined,
      message: message.trim(),
      sentAt: new Date().toISOString(),
    })
  }

  const borderFor = (hasError: boolean) =>
    hasError
      ? 'border-[var(--color-short)]/60 focus:border-[var(--color-short)]'
      : 'border-[var(--color-border)] focus:border-[var(--color-accent)]'

  return (
    <form id={formId} onSubmit={handleSubmit} noValidate className="space-y-5">
      <div className="space-y-1.5">
        <label htmlFor={emailId} className="block text-sm text-[var(--color-text-secondary)]">
          E-Mail-Adresse <span className="text-[var(--color-short)]">*</span>
        </label>
        <input
          id={emailId}
          type="email"
          value={email}
          disabled={disabled}
          onChange={(e) => {
            setEmail(e.target.value)
            if (errors.email) setErrors((p) => ({ ...p, email: undefined }))
          }}
          placeholder="name@example.com"
          aria-invalid={Boolean(errors.email)}
          className={`${fieldClass} ${borderFor(Boolean(errors.email))}`}
        />
        {errors.email ? (
          <p className="text-xs text-[var(--color-short)]">{errors.email}</p>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <label htmlFor={telegramId} className="block text-sm text-[var(--color-text-secondary)]">
          Telegram-Benutzername
        </label>
        <input
          id={telegramId}
          type="text"
          value={telegram}
          disabled={disabled}
          onChange={(e) => setTelegram(e.target.value)}
          placeholder="@deinname"
          className={`${fieldClass} ${borderFor(false)}`}
        />
        <p className="text-xs text-[var(--color-text-muted)]">
          Optional – erleichtert uns eine schnelle Kontaktaufnahme.
        </p>
      </div>

      <div className="space-y-1.5">
        <label htmlFor={messageId} className="block text-sm text-[var(--color-text-secondary)]">
          Nachricht <span className="text-[var(--color-short)]">*</span>
        </label>
        <textarea
          id={messageId}
          rows={6}
          value={message}
          disabled={disabled}
          maxLength={CONTACT_MESSAGE_MAX}
          onChange={(e) => {
            setMessage(e.target.value)
            if (errors.message) setErrors((p) => ({ ...p, message: undefined }))
          }}
          placeholder="Beschreibe dein Anliegen möglichst genau..."
          aria-invalid={Boolean(errors.message)}
          className={`${fieldClass} resize-y leading-relaxed ${borderFor(Boolean(errors.message))}`}
        />
        <div className="flex items-center justify-between gap-3">
          {errors.message ? (
            <p className="text-xs text-[var(--color-short)]">{errors.message}</p>
          ) : (
            <span />
          )}
          <CharacterCounter value={message.length} max={CONTACT_MESSAGE_MAX} />
        </div>
      </div>
    </form>
  )
}
