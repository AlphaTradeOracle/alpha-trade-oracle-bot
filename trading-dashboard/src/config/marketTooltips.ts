/**
 * Central copy for Market Sentiment metric tile tooltips.
 * Keys must match the `label` used in MarketRegimeCard.
 */
export const MARKET_TOOLTIPS = {
  'BTC Trend':
    'Bewertung des aktuellen Bitcoin-Trends anhand technischer Indikatoren und der Marktstruktur. Dient als Orientierung für die übergeordnete Trendrichtung.',
  'BTC Bias':
    'Zeigt die wahrscheinlichste Marktrichtung für Bitcoin (bullisch, neutral oder bärisch). Berücksichtigt mehrere Trend- und Marktindikatoren.',
  'BTC.D':
    'Bitcoin-Dominanz. Gibt an, wie groß der Anteil der gesamten Kryptomarktkapitalisierung ist, der auf Bitcoin entfällt. Eine steigende Dominanz spricht häufig für Kapitalzuflüsse in Bitcoin.',
  'USDT.D':
    'USDT-Dominanz. Zeigt den Anteil von Tether (USDT) an der gesamten Kryptomarktkapitalisierung. Steigende Werte deuten häufig auf eine defensive Marktstimmung und höhere Liquidität in Stablecoins hin.',
  Funding:
    'Die Funding-Rate zeigt, welche Marktseite aktuell dominiert. Positive Werte bedeuten, dass überwiegend auf steigende Kurse gesetzt wird, negative Werte sprechen für eine Mehrheit an Short-Positionen. Extreme Werte können ein Warnsignal für eine mögliche Trendwende sein.',
  'Fear & Greed':
    'Misst die aktuelle Marktstimmung auf einer Skala von extremer Angst bis extremer Gier. Niedrige Werte stehen für Angst, hohe Werte für Gier.',
  Liquidity:
    'Bewertet die aktuelle Marktliquidität anhand verschiedener Kennzahlen. Höhere Werte sprechen für ein liquideres Marktumfeld, niedrigere Werte können auf ein erhöhtes Risiko und geringere Handelsaktivität hindeuten.',
  'Global Score':
    'Gesamtbewertung aller analysierten Marktindikatoren. Fasst Trend-, Sentiment-, Volumen-, Liquiditäts- und Makrodaten zu einem einzigen Score zusammen.',
} as const

export type MarketTooltipKey = keyof typeof MARKET_TOOLTIPS

export function getMarketTooltip(label: string): string | undefined {
  return MARKET_TOOLTIPS[label as MarketTooltipKey]
}
