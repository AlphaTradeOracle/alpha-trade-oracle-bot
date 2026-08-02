import {
  ClipboardList,
  LayoutDashboard,
  CircleDot,
  Hourglass,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { BrandLockup } from '../components/brand/BrandLockup'

const nav = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/open', label: 'Open Trades', icon: CircleDot },
  { to: '/pending', label: 'Pending Trades', icon: Hourglass },
  { to: '/closed', label: 'Closed Trades', icon: ClipboardList },
]

interface SidebarProps {
  onNavigate?: () => void
}

export function Sidebar({ onNavigate }: SidebarProps) {
  return (
    <aside className="flex h-full w-[240px] flex-col border-r border-[var(--color-border-subtle)] bg-[var(--color-bg-elevated)]/90 backdrop-blur">
      <div className="border-b border-[color-mix(in_srgb,var(--color-accent)_35%,var(--color-border-subtle))] px-4 py-6">
        <BrandLockup size={114} />
      </div>

      <nav className="flex flex-1 flex-col gap-1 p-3">
        {nav.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={onNavigate}
            className={({ isActive }) =>
              [
                'relative flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm transition-colors',
                isActive
                  ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                  : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text-muted)]',
              ].join(' ')
            }
          >
            {({ isActive }) => (
              <>
                {isActive ? (
                  <span
                    aria-hidden
                    className="absolute top-1.5 bottom-1.5 left-0 w-[2px] rounded-full bg-[var(--color-accent)]"
                  />
                ) : null}
                <item.icon size={16} strokeWidth={1.8} />
                {item.label}
              </>
            )}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
