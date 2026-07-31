import type { TradeSide } from '../../types/trade'

export function SideBadge({ side }: { side: TradeSide }) {
  const isLong = side === 'LONG'
  return (
    <span
      className={[
        'inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-semibold tracking-wide',
        isLong
          ? 'border-[var(--color-long)]/30 bg-[var(--color-long-soft)] text-[var(--color-long)]'
          : 'border-[var(--color-short)]/30 bg-[var(--color-short-soft)] text-[var(--color-short)]',
      ].join(' ')}
    >
      {isLong ? 'LONG' : 'SHORT'}
    </span>
  )
}
