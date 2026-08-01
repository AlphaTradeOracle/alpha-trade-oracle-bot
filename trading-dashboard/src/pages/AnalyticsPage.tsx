import { useState } from 'react'
import {
  BarChart3,
  BookOpen,
  CalendarRange,
  Gauge,
  ShieldAlert,
  Target,
  TrendingUp,
} from 'lucide-react'
import { Button } from '../components/ui/Button'
import { Modal } from '../components/ui/Modal'
import { PageHeader } from '../components/ui/PageHeader'
import { PrototypeBanner } from '../components/ui/PrototypeBanner'

const modules = [
  {
    id: 'winrate',
    title: 'Winrate',
    blurb: 'Wins vs losses across closed trades.',
    icon: Target,
  },
  {
    id: 'pf',
    title: 'Profit Factor',
    blurb: 'Gross profit divided by gross loss.',
    icon: Gauge,
  },
  {
    id: 'avg-r',
    title: 'Average R',
    blurb: 'Mean R-multiple of the closed book.',
    icon: TrendingUp,
  },
  {
    id: 'monthly',
    title: 'Monthly Performance',
    blurb: 'Equity change grouped by month.',
    icon: CalendarRange,
  },
  {
    id: 'strategy',
    title: 'Strategy Performance',
    blurb: 'Breakdown by setup / score band.',
    icon: BarChart3,
  },
  {
    id: 'risk',
    title: 'Risk Management',
    blurb: 'Exposure, margin, and open R limits.',
    icon: ShieldAlert,
  },
  {
    id: 'journal',
    title: 'Journal',
    blurb: 'Trade notes and review checklist.',
    icon: BookOpen,
  },
] as const

/** Clickable analytics shell — charts/logic come later. */
export function AnalyticsPage() {
  const [active, setActive] = useState<(typeof modules)[number] | null>(null)

  return (
    <div>
      <PageHeader
        title="Analytics"
        subtitle="Prototype modules — open any card for a placeholder panel"
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {modules.map((mod) => (
          <button
            key={mod.id}
            type="button"
            onClick={() => setActive(mod)}
            className="panel flex gap-3 p-5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
          >
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
              <mod.icon size={18} strokeWidth={1.8} />
            </span>
            <span>
              <span className="block text-sm font-semibold text-[var(--color-text)]">
                {mod.title}
              </span>
              <span className="mt-1 block text-xs text-[var(--color-text-muted)]">
                {mod.blurb}
              </span>
            </span>
          </button>
        ))}
      </div>

      <Modal
        open={active !== null}
        title={active?.title ?? 'Analytics'}
        onClose={() => setActive(null)}
        footer={
          <Button variant="primary" onClick={() => setActive(null)}>
            Back
          </Button>
        }
      >
        <div className="space-y-3">
          <PrototypeBanner>
            UI shell only — calculations and charts will be added when you request them.
          </PrototypeBanner>
          <p>{active?.blurb}</p>
          <p className="text-xs text-[var(--color-text-muted)]">
            Module id: <span className="tabular">{active?.id}</span>
          </p>
        </div>
      </Modal>
    </div>
  )
}
