/** Small hint that an action is mock-only in the interactive MVP. */
export function PrototypeBanner({ children }: { children: string }) {
  return (
    <p className="rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--color-text-muted)]">
      {children}
    </p>
  )
}
