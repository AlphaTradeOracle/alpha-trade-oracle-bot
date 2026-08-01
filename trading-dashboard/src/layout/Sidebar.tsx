import {
  BarChart3,
  ClipboardList,
  LayoutDashboard,
  Settings,
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
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/settings', label: 'Settings', icon: Settings },
]

interface SidebarProps {
  onNavigate?: () => void
}

export function Sidebar({ onNavigate }: SidebarProps) {
  return (
    <aside className="flex h-full w-[240px] flex-col border-r border-[var(--color-border-subtle)] bg-[var(--color-bg-elevated)]/90 backdrop-blur">
      <div className="border-b border-[var(--color-border-subtle)] px-5 py-5">
        <BrandLockup size={40} />
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
                'flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm transition-colors',
                isActive
                  ? 'bg-[var(--color-accent-soft)] text-[var(--color-text)]'
                  : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text)]',
              ].join(' ')
            }
          >
            <item.icon size={16} strokeWidth={1.8} />
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-[var(--color-border-subtle)] p-4">
        <p className="text-[11px] leading-relaxed text-[var(--color-text-muted)]">
          Mock JSON source · ready for exchange APIs & live refresh.
        </p>
      </div>
    </aside>
  )
}
