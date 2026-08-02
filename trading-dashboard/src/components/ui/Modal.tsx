import { useEffect, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { Button } from './Button'

interface ModalProps {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  /** `md`/`lg` for dialogs, `xl` for full workspace views */
  size?: 'md' | 'lg' | 'xl'
  /** Optional line under the title */
  subtitle?: string
}

const sizeClass: Record<NonNullable<ModalProps['size']>, string> = {
  md: 'max-w-lg',
  lg: 'max-w-2xl',
  xl: 'w-[92vw] max-w-[1440px]',
}

/**
 * Lightweight modal for prototype interactions.
 * Rendered inline instead of through a portal to keep dependencies small.
 */
export function Modal({
  open,
  title,
  subtitle,
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
    // Prevent the page behind the dialog from scrolling along.
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = previousOverflow
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4">
      <button
        type="button"
        aria-label="Close dialog backdrop"
        className="modal-backdrop absolute inset-0 bg-black/60 backdrop-blur-[3px]"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={[
          'modal-panel relative z-10 flex max-h-[92vh] w-full flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-2xl',
          sizeClass[size],
        ].join(' ')}
      >
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-[var(--color-border-subtle)] px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold tracking-tight">{title}</h2>
            {subtitle ? (
              <p className="mt-0.5 text-xs leading-relaxed text-[var(--color-text-muted)]">
                {subtitle}
              </p>
            ) : null}
          </div>
          <Button variant="ghost" className="!px-2 !py-1.5" onClick={onClose} aria-label="Close">
            <X size={16} />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 text-sm text-[var(--color-text-secondary)]">
          {children}
        </div>

        {footer ? (
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-[var(--color-border-subtle)] px-5 py-3">
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  )
}
