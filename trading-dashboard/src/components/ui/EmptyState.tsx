interface EmptyStateProps {
  title: string
  description?: string
}

export function EmptyState({ title, description }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
      <p className="text-sm font-medium text-[var(--color-text-secondary)]">{title}</p>
      {description ? (
        <p className="max-w-sm text-xs text-[var(--color-text-muted)]">{description}</p>
      ) : null}
    </div>
  )
}
