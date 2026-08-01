import { RefreshCw } from 'lucide-react'
import { useDeskData } from '../../context/DeskDataContext'
import { Button } from './Button'

/** Manual desk refresh — live data also reloads on tab change and on a quiet interval. */
export function DeskActions() {
  const { refresh, loading, error } = useDeskData()

  return (
    <div className="flex flex-col items-end gap-1.5">
      <Button
        variant="primary"
        onClick={() => void refresh()}
        disabled={loading}
        className="min-w-[9.5rem] shadow-[0_0_0_1px_color-mix(in_srgb,var(--color-accent)_35%,transparent),0_8px_20px_-12px_var(--color-accent)]"
        aria-label="Desk-Daten aktualisieren"
      >
        <RefreshCw size={15} className={loading ? 'animate-spin' : undefined} />
        {loading ? 'Aktualisiert…' : 'Aktualisieren'}
      </Button>
      {error ? (
        <p className="max-w-[16rem] text-right text-xs text-[var(--color-short)]">{error}</p>
      ) : null}
    </div>
  )
}
