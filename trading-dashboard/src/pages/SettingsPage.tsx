import { PageHeader } from '../components/ui/PageHeader'

/** Placeholder — future API keys, refresh intervals, notification channels. */
export function SettingsPage() {
  return (
    <div>
      <PageHeader
        title="Settings"
        subtitle="Local configuration · no auth · no remote secrets required"
      />
      <div className="panel space-y-4 p-8">
        <p className="text-sm text-[var(--color-text-secondary)]">
          Extension points ready for later integration:
        </p>
        <ul className="list-inside list-disc space-y-1.5 text-sm text-[var(--color-text-muted)]">
          <li>Binance / Bybit / Hyperliquid market + account adapters</li>
          <li>Discord webhooks & Telegram notifications</li>
          <li>Live mark refresh / auto polling</li>
          <li>Risk limits & journal attachments</li>
        </ul>
      </div>
    </div>
  )
}
