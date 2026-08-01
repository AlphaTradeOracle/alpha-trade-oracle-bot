import { useEffect, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { Button } from './Button'

interface ModalProps {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  /** Narrower dialog for simple confirmations */
  size?: 'md' | 'lg'
}

/**
 * Lightweight modal for prototype interactions.
 * No portal library — keep the MVP dependency surface small.
 */
export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  size = 'md',
}: ModalProps) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close dialog backdrop"
        className="absolute inset-0 bg-black/55 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={[
          'relative z-10 w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-2xl',
          size === 'lg' ? 'max-w-2xl' : 'max-w-lg',
        ].join(' ')}
      >
        <div className="flex items-center justify-between border-b border-[var(--color-border-subtle)] px-5 py-4">
          <h2 className="text-base font-semibold tracking-tight">{title}</h2>
          <Button variant="ghost" className="!px-2 !py-1.5" onClick={onClose} aria-label="Close">
            <X size={16} />
          </Button>
        </div>
        <div className="px-5 py-4 text-sm text-[var(--color-text-secondary)]">{children}</div>
        {footer ? (
          <div className="flex items-center justify-end gap-2 border-t border-[var(--color-border-subtle)] px-5 py-3">
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  )
}
