import { CheckCircle2 } from 'lucide-react'

interface SuccessMessageProps {
  title?: string
  description?: string
}

/** Confirmation shown after a contact message was accepted. */
export function SuccessMessage({
  title = 'Vielen Dank!',
  description = 'Deine Nachricht wurde erfolgreich übermittelt.',
}: SuccessMessageProps) {
  return (
    <div className="flex flex-col items-center gap-3 py-6 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-long-soft)] text-[var(--color-long)]">
        <CheckCircle2 size={24} strokeWidth={1.8} />
      </span>
      <div>
        <p className="text-base font-semibold text-[var(--color-text)]">{title}</p>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">{description}</p>
      </div>
    </div>
  )
}
