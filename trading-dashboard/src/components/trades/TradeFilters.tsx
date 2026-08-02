import { Search } from 'lucide-react'
import type { TradeFilterState } from '../../types/trade'

interface TradeFiltersProps {
  filters: TradeFilterState
  onChange: (next: TradeFilterState) => void
  /** Hide PnL filter for pending tables */
  showPnlFilter?: boolean
  sortOptions?: { value: TradeFilterState['sortBy']; label: string }[]
}

const selectClass =
  'h-9 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-3 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]'

export function TradeFilters({
  filters,
  onChange,
  showPnlFilter = true,
  sortOptions = [
    { value: 'openedAt', label: 'Opened' },
    { value: 'symbol', label: 'Symbol' },
    { value: 'score', label: 'Score' },
    { value: 'r', label: 'R' },
  ],
}: TradeFiltersProps) {
  const patch = (partial: Partial<TradeFilterState>) => onChange({ ...filters, ...partial })

  return (
    <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <label className="relative block w-full max-w-sm">
        <Search
          size={15}
          className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-[var(--color-text-muted)]"
        />
        <input
          value={filters.query}
          onChange={(e) => patch({ query: e.target.value })}
          placeholder="Search symbol or id…"
          className="h-9 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] pr-3 pl-9 text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent)]"
        />
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <select
          className={selectClass}
          value={filters.side}
          onChange={(e) => patch({ side: e.target.value as TradeFilterState['side'] })}
        >
          <option value="all">All sides</option>
          <option value="LONG">Long</option>
          <option value="SHORT">Short</option>
        </select>

        <select
          className={selectClass}
          value={String(filters.minScore)}
          onChange={(e) => patch({ minScore: Number(e.target.value) })}
        >
          <option value="0">Score ≥ 0</option>
          <option value="20">Score ≥ 20</option>
          <option value="40">Score ≥ 40</option>
          <option value="60">Score ≥ 60</option>
          <option value="75">Score ≥ 75</option>
        </select>

        {showPnlFilter ? (
          <select
            className={selectClass}
            value={filters.pnl}
            onChange={(e) => patch({ pnl: e.target.value as TradeFilterState['pnl'] })}
          >
            <option value="all">All PnL</option>
            <option value="profit">Profit</option>
            <option value="loss">Loss</option>
          </select>
        ) : null}

        <select
          className={selectClass}
          value={filters.sortBy}
          onChange={(e) => patch({ sortBy: e.target.value as TradeFilterState['sortBy'] })}
        >
          {sortOptions.map((o) => (
            <option key={o.value} value={o.value}>
              Sort: {o.label}
            </option>
          ))}
        </select>

        <button
          type="button"
          className="h-9 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-3 text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-text)]"
          onClick={() =>
            patch({ sortDir: filters.sortDir === 'asc' ? 'desc' : 'asc' })
          }
        >
          {filters.sortDir === 'asc' ? 'Asc' : 'Desc'}
        </button>
      </div>
    </div>
  )
}
