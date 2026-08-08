interface CharacterCounterProps {
  value: number
  max: number
}

/** Live counter shown below long text inputs. */
export function CharacterCounter({ value, max }: CharacterCounterProps) {
  const ratio = value / max
  const tone =
    ratio >= 1
      ? 'text-[var(--color-short)]'
      : ratio > 0.9
        ? 'text-[var(--color-warn)]'
        : 'text-[var(--color-text-muted)]'

  return (
    <span className={`tabular text-[11px] ${tone}`}>
      {value.toLocaleString('de-DE')} / {max.toLocaleString('de-DE')} Zeichen
    </span>
  )
}
