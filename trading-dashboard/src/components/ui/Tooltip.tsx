import {
  useEffect,
  useId,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
  type ReactNode,
} from 'react'

interface TooltipProps {
  /** Plain-text description shown above the trigger. */
  content: string
  children: ReactNode
  className?: string
  /**
   * When true (default), the wrapper is keyboard-focusable.
   * Set false when wrapping a native control (e.g. button) to avoid nested tabs.
   */
  keyboardFocus?: boolean
}

function prefersHover(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return true
  }
  return window.matchMedia('(hover: hover) and (pointer: fine)').matches
}

/**
 * Lightweight accessible tooltip — hover/focus on desktop, tap-to-toggle on touch.
 * Absolutely positioned above the trigger; does not shift layout.
 */
export function Tooltip({
  content,
  children,
  className = '',
  keyboardFocus = true,
}: TooltipProps) {
  const tipId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return

    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    const onPointerDown = (event: PointerEvent) => {
      const root = rootRef.current
      if (root && !root.contains(event.target as Node)) {
        setOpen(false)
      }
    }

    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('pointerdown', onPointerDown, true)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('pointerdown', onPointerDown, true)
    }
  }, [open])

  const show = () => setOpen(true)
  const hide = () => setOpen(false)

  const onBlurCapture = (event: FocusEvent<HTMLDivElement>) => {
    const next = event.relatedTarget as Node | null
    if (!rootRef.current?.contains(next)) {
      hide()
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      hide()
      ;(event.currentTarget as HTMLElement).blur()
    }
  }

  const onClick = () => {
    // Touch / coarse pointers: tap toggles. Fine pointer uses hover only.
    if (prefersHover()) return
    setOpen((prev) => !prev)
  }

  return (
    <div
      ref={rootRef}
      className={['relative w-full', className].filter(Boolean).join(' ')}
      tabIndex={keyboardFocus ? 0 : undefined}
      aria-describedby={open ? tipId : undefined}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocusCapture={show}
      onBlurCapture={onBlurCapture}
      onKeyDown={onKeyDown}
      onClick={onClick}
    >
      {children}

      <div
        id={tipId}
        role="tooltip"
        data-open={open ? 'true' : 'false'}
        aria-hidden={!open}
        className="ui-tooltip"
      >
        {content}
      </div>
    </div>
  )
}
