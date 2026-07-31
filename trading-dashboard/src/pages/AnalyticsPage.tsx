import { PageHeader } from '../components/ui/PageHeader'

/** Placeholder — wire Winrate / PF / monthly / strategy breakdowns later. */
export function AnalyticsPage() {
  return (
    <div>
      <PageHeader
        title="Analytics"
        subtitle="Performance analytics will land here without changing the shell."
      />
      <div className="panel p-8">
        <p className="text-sm text-[var(--color-text-secondary)]">
          Planned modules: Winrate, Profit Factor, Average R, Monthly Performance,
          Strategy Performance, Risk Management, and Journal notes.
        </p>
      </div>
    </div>
  )
}
