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

const modules = [
  {
    id: 'winrate',
    title: 'Winrate',
    blurb: 'Gewinner gegen Verlierer über alle geschlossenen Trades.',
    icon: Target,
  },
  {
    id: 'pf',
    title: 'Profit Factor',
    blurb: 'Bruttogewinn geteilt durch Bruttoverlust.',
    icon: Gauge,
  },
  {
    id: 'avg-r',
    title: 'Avg Profit %',
    blurb: 'Mittlerer Margin-Profit in Prozent des geschlossenen Buchs.',
    icon: TrendingUp,
  },
  {
    id: 'monthly',
    title: 'Monthly Performance',
    blurb: 'Kapitalentwicklung nach Monaten gruppiert.',
    icon: CalendarRange,
  },
  {
    id: 'strategy',
    title: 'Strategy Performance',
    blurb: 'Auswertung nach Setup und Score-Band.',
    icon: BarChart3,
  },
  {
    id: 'risk',
    title: 'Risk Management',
    blurb: 'Exposure, Margin und offener Profit im Blick.',
    icon: ShieldAlert,
  },
  {
    id: 'journal',
    title: 'Journal',
    blurb: 'Notizen und Review-Checkliste zu jedem Trade.',
    icon: BookOpen,
  },
] as const

export function AnalyticsPage() {
  const [active, setActive] = useState<(typeof modules)[number] | null>(null)

  return (
    <div>
      <PageHeader
        title="Analytics"
        subtitle="Auswertungen zu Performance, Risiko und Strategie"
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {modules.map((mod) => (
          <button
            key={mod.id}
            type="button"
            onClick={() => setActive(mod)}
            className="panel flex cursor-pointer gap-3 p-5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
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
        subtitle={active?.blurb}
        onClose={() => setActive(null)}
        footer={
          <Button variant="primary" onClick={() => setActive(null)}>
            Zurück
          </Button>
        }
      >
        <p>
          Die Auswertung wird aus dem geschlossenen Handelsbuch berechnet und hier
          detailliert dargestellt.
        </p>
      </Modal>
    </div>
  )
}
