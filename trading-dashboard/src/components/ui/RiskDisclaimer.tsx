import { AlertTriangle } from 'lucide-react'

/**
 * Legally worded risk notice for leveraged trading.
 * Displayed directly above the footer on every page.
 */
export function RiskDisclaimer() {
  return (
    <section
      aria-labelledby="risk-disclaimer-title"
      className="rounded-xl border border-[var(--color-warn)]/25 bg-[var(--color-warn-soft)]/40 p-4 sm:p-5"
    >
      <div className="flex gap-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-warn-soft)] text-[var(--color-warn)]">
          <AlertTriangle size={16} strokeWidth={2} />
        </span>

        <div className="min-w-0 space-y-2">
          <h2
            id="risk-disclaimer-title"
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-warn)]"
          >
            Risikohinweis
          </h2>

          <div className="space-y-2 text-xs leading-relaxed text-[var(--color-text-secondary)] sm:text-[13px]">
            <p>
              Der Handel mit Finanzinstrumenten, insbesondere mit Hebelprodukten
              (Leverage), ist mit erheblichen Risiken verbunden und eignet sich nicht
              für jeden Anleger. Es besteht das Risiko erheblicher finanzieller Verluste
              bis hin zum vollständigen Verlust des eingesetzten Kapitals.
            </p>
            <p>
              Historische Wertentwicklungen, Backtests oder frühere Handelsergebnisse
              stellen keine Garantie für zukünftige Ergebnisse dar.
            </p>
            <p>
              Sämtliche auf dieser Plattform dargestellten Informationen dienen
              ausschließlich Informationszwecken und stellen weder eine Finanz-,
              Anlage-, Rechts- noch Steuerberatung dar. Jeder Nutzer handelt
              eigenverantwortlich und trägt das vollständige Risiko seiner
              Handelsentscheidungen.
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}
