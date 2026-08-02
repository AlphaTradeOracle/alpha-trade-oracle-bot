import { Menu, X } from 'lucide-react'
import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { BrandLockup } from '../components/brand/BrandLockup'
import { Footer } from './Footer'
import { Sidebar } from './Sidebar'

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="flex h-full min-h-0">
      {/* Desktop sidebar */}
      <div className="hidden md:block">
        <Sidebar />
      </div>

      {/* Mobile drawer */}
      {mobileOpen ? (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            type="button"
            aria-label="Close menu"
            className="absolute inset-0 bg-black/55"
            onClick={() => setMobileOpen(false)}
          />
          <div className="relative z-50 h-full w-[240px] shadow-xl">
            <Sidebar onNavigate={() => setMobileOpen(false)} />
          </div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-[var(--color-border-subtle)] px-4 py-3 md:hidden">
          <button
            type="button"
            className="rounded-lg border border-[var(--color-border)] p-2 text-[var(--color-text-secondary)]"
            onClick={() => setMobileOpen((v) => !v)}
            aria-label="Toggle menu"
          >
            {mobileOpen ? <X size={18} /> : <Menu size={18} />}
          </button>
          <BrandLockup size={36} stacked={false} />
        </header>

        {/*
          Sticky footer: the scroll container is a column flex box, the content
          area grows, so the footer sits at the viewport bottom on short pages
          and directly below the content on long ones.
        */}
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          <main className="flex-1">
            <div className="mx-auto w-full max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8">
              <Outlet />
            </div>
          </main>
          <Footer />
        </div>
      </div>
    </div>
  )
}
