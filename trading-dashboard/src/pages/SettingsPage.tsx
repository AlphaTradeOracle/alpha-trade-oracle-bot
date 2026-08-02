import { useState } from 'react'
import { Button } from '../components/ui/Button'
import { Modal } from '../components/ui/Modal'
import { PageHeader } from '../components/ui/PageHeader'
import { useSettings } from '../hooks/useSettings'

export function SettingsPage() {
  const { settings, update, save, savedAt } = useSettings()
  const [showSaved, setShowSaved] = useState(false)

  return (
    <div>
      <PageHeader
        title="Settings"
        subtitle="Konfiguration des Desks"
        actions={
          <Button
            variant="primary"
            onClick={() => {
              save()
              setShowSaved(true)
            }}
          >
            Speichern
          </Button>
        }
      />

      <div className="panel space-y-6 p-6 sm:p-8">
        <div className="grid gap-5 sm:grid-cols-2">
          <label className="block space-y-1.5 text-sm">
            <span className="text-[var(--color-text-secondary)]">Börsenanbindung</span>
            <select
              value={settings.exchange}
              onChange={(e) =>
                update({ exchange: e.target.value as typeof settings.exchange })
              }
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]"
            >
              <option value="mock">Interne Daten</option>
              <option value="binance">Binance</option>
              <option value="bybit">Bybit</option>
              <option value="hyperliquid">Hyperliquid</option>
            </select>
          </label>

          <label className="block space-y-1.5 text-sm">
            <span className="text-[var(--color-text-secondary)]">Aktualisierung (Sekunden)</span>
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
            Auto Refresh
          </label>

          <label className="block space-y-1.5 text-sm sm:col-span-2">
            <span className="text-[var(--color-text-secondary)]">Discord Webhook URL</span>
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
            Telegram-Benachrichtigungen
          </label>

          <label className="block space-y-1.5 text-sm">
            <span className="text-[var(--color-text-secondary)]">Standardrisiko (R)</span>
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
        title="Einstellungen gespeichert"
        onClose={() => setShowSaved(false)}
        footer={
          <Button variant="primary" onClick={() => setShowSaved(false)}>
            Weiter
          </Button>
        }
      >
        <div className="space-y-2">
          <p>
            Aktive Anbindung:{' '}
            <span className="text-[var(--color-text)]">{settings.exchange}</span>
          </p>
          <p className="text-xs text-[var(--color-text-muted)]">
            Zuletzt gespeichert: {savedAt ? new Date(savedAt).toLocaleString('de-DE') : '—'}
          </p>
        </div>
      </Modal>
    </div>
  )
}
