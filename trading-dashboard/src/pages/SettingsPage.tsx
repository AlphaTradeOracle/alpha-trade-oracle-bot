import { useState } from 'react'
import { Button } from '../components/ui/Button'
import { Modal } from '../components/ui/Modal'
import { PageHeader } from '../components/ui/PageHeader'
import { PrototypeBanner } from '../components/ui/PrototypeBanner'
import { useSettings } from '../hooks/useSettings'

/** Interactive settings prototype — values stay in memory (mock). */
export function SettingsPage() {
  const { settings, update, save, savedAt } = useSettings()
  const [showSaved, setShowSaved] = useState(false)

  return (
    <div>
      <PageHeader
        title="Settings"
        subtitle="Local prototype controls · no auth · no remote secrets"
        actions={
          <Button
            variant="primary"
            onClick={() => {
              save()
              setShowSaved(true)
            }}
          >
            Save settings
          </Button>
        }
      />

      <div className="panel space-y-6 p-6 sm:p-8">
        <PrototypeBanner>
          Changes are mock-only and reset on reload until persistence is added.
        </PrototypeBanner>

        <div className="grid gap-5 sm:grid-cols-2">
          <label className="block space-y-1.5 text-sm">
            <span className="text-[var(--color-text-secondary)]">Exchange adapter</span>
            <select
              value={settings.exchange}
              onChange={(e) =>
                update({ exchange: e.target.value as typeof settings.exchange })
              }
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]"
            >
              <option value="mock">Mock JSON</option>
              <option value="binance">Binance (later)</option>
              <option value="bybit">Bybit (later)</option>
              <option value="hyperliquid">Hyperliquid (later)</option>
            </select>
          </label>

          <label className="block space-y-1.5 text-sm">
            <span className="text-[var(--color-text-secondary)]">Refresh interval (sec)</span>
            <input
              type="number"
              min={5}
              max={3600}
              value={settings.refreshSeconds}
              onChange={(e) => update({ refreshSeconds: Number(e.target.value) || 30 })}
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 tabular text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]"
            />
          </label>

          <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)] sm:col-span-2">
            <input
              type="checkbox"
              checked={settings.autoRefresh}
              onChange={(e) => update({ autoRefresh: e.target.checked })}
              className="accent-[var(--color-accent)]"
            />
            Auto refresh (UI toggle only)
          </label>

          <label className="block space-y-1.5 text-sm sm:col-span-2">
            <span className="text-[var(--color-text-secondary)]">Discord webhook URL</span>
            <input
              type="url"
              placeholder="https://discord.com/api/webhooks/…"
              value={settings.discordWebhook}
              onChange={(e) => update({ discordWebhook: e.target.value })}
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]"
            />
          </label>

          <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
            <input
              type="checkbox"
              checked={settings.telegramEnabled}
              onChange={(e) => update({ telegramEnabled: e.target.checked })}
              className="accent-[var(--color-accent)]"
            />
            Telegram notifications (later)
          </label>

          <label className="block space-y-1.5 text-sm">
            <span className="text-[var(--color-text-secondary)]">Default risk (R)</span>
            <input
              type="number"
              min={0.1}
              step={0.1}
              value={settings.defaultRiskR}
              onChange={(e) => update({ defaultRiskR: Number(e.target.value) || 1 })}
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 tabular text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]"
            />
          </label>
        </div>
      </div>

      <Modal
        open={showSaved}
        title="Settings saved"
        onClose={() => setShowSaved(false)}
        footer={
          <Button variant="primary" onClick={() => setShowSaved(false)}>
            Continue
          </Button>
        }
      >
        <div className="space-y-3">
          <PrototypeBanner>
            Mock save complete — nothing was written to disk or a remote API.
          </PrototypeBanner>
          <p className="text-xs text-[var(--color-text-muted)]">
            Timestamp: {savedAt ?? '—'}
          </p>
          <p>
            Active adapter: <span className="text-[var(--color-text)]">{settings.exchange}</span>
          </p>
        </div>
      </Modal>
    </div>
  )
}
