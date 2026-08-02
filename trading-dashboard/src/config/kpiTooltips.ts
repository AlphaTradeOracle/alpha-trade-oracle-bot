/**
 * Central copy for KPI tile tooltips.
 * Keys must match the `title` used in KpiGrid / PerformanceKpiCard.
 */
export const KPI_TOOLTIPS = {
  Startkapital:
    'Das ursprünglich eingezahlte Kapital, mit dem dein Konto gestartet wurde.',
  Equity:
    'Aktueller Gesamtwert deines Kontos. Beinhaltet Cash sowie Gewinne und Verluste aus allen offenen Positionen.',
  Cash:
    'Verfügbares Guthaben für neue Trades. Kapital, das aktuell nicht in offenen Positionen gebunden ist.',
  'Realized PnL':
    'Bereits realisierte Gewinne oder Verluste aus geschlossenen Trades. Dieser Wert ändert sich nur, wenn eine Position geschlossen wird.',
  'Total Return':
    'Gesamtrendite seit Kontoeröffnung. Vergleicht deine aktuelle Equity prozentual mit dem Startkapital.',
  'Open uPnL':
    'Nicht realisierte Gewinne oder Verluste deiner aktuell offenen Positionen. Dieser Wert verändert sich fortlaufend mit dem Markt.',
  Winrate:
    'Prozentualer Anteil aller erfolgreich abgeschlossenen Trades. Eine hohe Winrate bedeutet nicht automatisch eine hohe Profitabilität.',
  'Margin Locked':
    'Kapital, das aktuell für offene Positionen gebunden ist. Es steht erst nach dem Schließen der Positionen wieder vollständig zur Verfügung.',
  'Open Positions':
    'Anzahl der derzeit offenen Positionen. Diese beeinflussen deine Equity und den Open uPnL.',
  'Pending Orders':
    'Anzahl aller noch nicht ausgeführten Kauf- oder Verkaufsaufträge. Sie werden ausgeführt, sobald der Markt den festgelegten Preis erreicht.',
  'Closed Trades': 'Gesamtzahl aller abgeschlossenen Trades.',
  Performance:
    'Entwicklung deines Kontos über verschiedene Zeiträume (1H, 24H, 7D, 30D). Zeigt die prozentuale Veränderung der Equity im jeweiligen Zeitraum.',
} as const

export type KpiTooltipKey = keyof typeof KPI_TOOLTIPS

export function getKpiTooltip(title: string): string | undefined {
  return KPI_TOOLTIPS[title as KpiTooltipKey]
}
