## 1. Strategie-Urteil

Die Strategie ist aktuell nicht profitabel validiert: Paper liegt bei ca. −88 USD, mit vielen Full-Stops und wenigen realisierten Gewinnern. Dass 2/4/6R im Exit-Replay auf denselben Entries am besten war, ist nur ein Hinweis, kein Beweis für Edge, weil es leicht overfittet sein kann. Das Problem sitzt wahrscheinlich primär vor dem Exit: Entry-Qualität, Regime-Erkennung, Stop-Distanz und Asset-Auswahl. Die Filter sind streng genug für weniger Volumen, aber nicht zwingend für höhere Expectancy, weil Score/STRONG offenbar nicht sauber gegen echte Forward-PnL kalibriert ist. Viele MTM-positive Trades wie XAVA/SOON deuten darauf hin, dass Richtung teilweise passt, aber Timing, Teilgewinn-Trigger oder Trade-Management nicht robust genug sind. 10x Leverage mit kleinen Stops und Gebühren macht Fehler teuer; jeder schlechte Fill, Spread oder Slippage frisst Edge. Fokus sollte jetzt nicht auf mehr Signalen liegen, sondern auf Trade-Qualität pro Regime, pro Asset und pro Setup-Typ.

---

## 2. Top-Schwächen, die PnL killen

### 1. Score ist wahrscheinlich nicht PnL-kalibriert
Score ≥75 bzw. Short ≤25 klingt logisch, aber ohne Mapping auf Forward-Expectancy ist es nur ein Heuristik-Filter. STRONG muss nachweislich bessere R-Multiple-Verteilung haben als normale Signale.

### 2. Entry kommt vermutlich zu spät oder in schlechte Pullbacks
ADX ≥20 und Multi-TF-Trend können Trends erkennen, aber auch spät einsteigen lassen. Viele Full-Stops sprechen für schlechte Entry-Location oder Stop zu nah an normaler Volatilität.

### 3. Universe zu breit für KuCoin-Realität
Top 100 Market Cap ≠ gute KuCoin-Tradability. Illiquide oder exotische Coins erzeugen Spread, Gaps, Wick-Stops und schlechte Fills. SCUSDT Skip zeigt auch Universe/Exchange-Mismatch.

### 4. Exit-Optimierung auf denselben Entries ist fragil
2/4/6R war im Replay besser, aber der Vorsprung ist klein. Ohne Walk-forward/OOS ist das kein stabiler Parameter, sondern potenziell Curve Fit.

### 5. BE nach TP1 kann Winner kappen
BE nach TP1 ist defensiv sinnvoll, aber wenn TP1 weit entfernt liegt und Trades oft vorher stoppen, hilft es kaum. Wenn Trades erst MTM-positiv sind und dann Full-Stop laufen, fehlt eventuell ein früheres De-Risking oder eine Time/Failure-Regel.

### 6. Kostenmodell vermutlich zu sauber
0,1% Fee ist drin, aber Slippage, Spread, Funding, Partial Fill und Mark/Last-Differenzen können bei 10x und Small Caps erheblich sein.

### 7. Short-Score-Semantik ist gefährlich
Config sagt Short bei Score ≤25, Beispiel sagt STRONG SHORT Score 82. Das muss eindeutig getrennt werden: Rohscore vs. Strength-Score. Sonst entstehen falsche Telegram-Signale und falsche Auswertungen.

---

## 3. Priorisierte Fixes

| Prio | Änderung | Warum | Validierung | Effort |
|---|---|---|---|---|
| P0 | Score in Direction-Score und Strength-Score trennen | Short-Logik aktuell missverständlich; vermeidet falsche Reports und falsche Analytics | Prüfen: jedes Short-Signal hat bearish raw score oder separate strength korrekt geloggt | S |
| P0 | Trade-Log auf R-Basis erweitern: MFE, MAE, Time-to-MFE, Time-to-Stop, Fill-Preis, Spread-Schätzung | Ohne MFE/MAE weißt du nicht, ob Entry, SL oder Exit schuld ist | Nach 50–100 Trades: Verteilung pro Setup/Asset/Regime analysieren | M |
| P0 | Universe auf echte KuCoin-Tradability filtern | Reduziert Wick-Stops, Slippage, Skips und illiquide Namen | Mindestvolumen, Spread, Listing-Status, verfügbare Paare; SCUSDT-artige Skips = 0 | S/M |
| P0 | Walk-forward-Test für 2/4/6R statt Replay auf denselben Entries | Verhindert Overfit der Exit-Ladder | Train-Zeitraum Parameter wählen, OOS-Zeitraum unverändert testen | M |
| P1 | Entry-Quality-Filter ergänzen: kein Entry nach überdehntem Move, Pullback/Retest bevorzugen | Viele Full-Stops deuten auf schlechte Entry-Location | Vergleich: aktuelle Entries vs. Retest-Entries nach MFE/MAE und Hit-Rate TP1 | M |
| P1 | Stop-Regel kalibrieren nach ATR/Struktur und Asset-Volatilität | Ein fixer Struktur/ATR-Mix kann je Coin zu eng/weit sein | Stop-out-Rate, MAE vor Winner, Stop-Effizienz pro Coin messen | M |
| P1 | Regime-Filter verschärfen: Trendqualität statt nur ADX ≥20 | ADX erkennt Stärke, aber nicht zwingend saubere Fortsetzung | Test mit ADX-Slope, EMA-Alignment, Higher-High/Lower-Low-Struktur | M |
| P1 | Time-Stop / Failure-Exit testen | Wenn Trade nach X Kerzen nicht in Richtung läuft, Kapital freigeben und Full-Stops reduzieren | Expectancy mit/ohne Time-Stop vergleichen | S/M |
| P1 | Pre-TP De-Risking testen, aber nur datenbasiert | Viele Trades MTM-positiv, dann Stop: eventuell kleiner Teil bei 1R sinnvoll | Test: 1R/3R/5R vs. 2/4/6R vs. BE-Regeln | M |
| P2 | Per-Asset Allowlist/Blocklist nach Expectancy | Manche Coins killen PnL systematisch | Nur Assets mit positiver OOS-R-Verteilung handeln | S |
| P2 | Separate Long/Short-Modelle auswerten | Short-Edge und Long-Edge sind in Crypto oft unterschiedlich | R-Verteilung getrennt nach Richtung | S |
| P2 | Telegram komprimieren | Bessere Lesbarkeit, weniger Fehler, schnellere Entscheidung | User-Feedback, weniger Missverständnisse | S |

---

## 4. Top 5 Experimente nach Impact/Effort

| Rang | Experiment | Impact | Effort | Ziel |
|---|---|---:|---:|---|
| 1 | MFE/MAE-Analyse aller Paper-Trades | Hoch | M | Klären, ob Entry, SL oder Exit das Hauptproblem ist |
| 2 | KuCoin-Liquiditätsfilter: Spread/Volumen/Pair-Verfügbarkeit | Hoch | S/M | Schlechte Fills und nicht handelbare Signale entfernen |
| 3 | Walk-forward-Test 2/4/6R vs. 1/3/5R vs. 1/2/4R vs. Trail | Hoch | M | Exit-Regel robust statt overfitted wählen |
| 4 | Entry nur nach Pullback/Retest statt direktem Momentum-Chase | Hoch | M | Weniger Full-Stops, bessere R-Location |
| 5 | Time-Stop: Exit, wenn nach 2–4 Kerzen kein Fortschritt Richtung TP1 | Mittel/Hoch | S/M | Kapitalbindung und späte Full-Stops reduzieren |

---

## 5. Was wir NICHT tun sollten

- Nicht Score-Min senken, nur um mehr Signale zu bekommen.  
- Nicht 2/4/6R als endgültig betrachten, nur weil es auf denselben Entries am besten war.  
- Nicht LLM in Trade-Entscheidungen einbauen, solange es nur Prosa liefern soll.  
- Nicht Leverage erhöhen, solange Expectancy negativ oder unbewiesen ist.  
- Nicht weitere Indikatoren stapeln, bevor MFE/MAE und Regime-Attribution sauber sind.  
- Nicht aus MTM-positiven Trades automatisch schließen, dass das Setup gut ist; realisierte R zählt.  
- Nicht illiquide KuCoin-Paare handeln, nur weil sie im Market-Cap-Top-100-Universum sind.  
- Nicht Long und Short zusammen bewerten; beide brauchen getrennte Kennzahlen.

---

## 6. Cleanes Telegram-Signal

### Visuelle Regeln

**Aufs Chart**
- Candles 1h Primary  
- Entry-Zone als Band  
- SL-Linie klar rot  
- TP1/TP2/TP3 klar grün  
- Aktueller Preis  
- Optional: 1h Trend/EMA oder Strukturlevel  
- Timestamp und Symbol klein

**In den Text**
- Symbol, Richtung, Strength/Score  
- Timeframe  
- Entry, SL, TP1–3  
- Scale-out  
- BE-Regel  
- Invalidation/Expiry  
- Kurzer Hinweis, falls TPs auf Mid-Entry berechnet sind

**Weglassen**
- Lange LLM-Einordnung  
- Doppelte Confidence/Staerke-Felder  
- Zu viele Bestätigungen  
- Voller Disclaimer-Block  
- Lange Datenqualitätsprosa  
- RR-Erklärungen, wenn TP bereits in R angegeben ist

---

### Wichtig zur Berechnung

Für die Beispielwerte rechne ich TPs auf **Mid-Entry 0,2325**.  
Short-Risk: `SL 0,248 − Entry 0,2325 = 0,0155 R`

Daraus:
- TP1 2R: `0,2325 − 2 × 0,0155 = 0,2015`
- TP2 4R: `0,1705`
- TP3 6R: `0,1395`

Wenn Fill bei 0,230 oder 0,235 liegt, müssen TPs neu berechnet werden.

---

### Kompakte Caption / Folge-Nachricht

```text
XAVA/USDT  STRONG SHORT
Strength 82  TF 1h

Entry 0,230–0,235
SL 0,248

TP1 0,2015  2R
TP2 0,1705  4R
TP3 0,1395  6R

Scale 33/33/34
Nach TP1 Stop auf BE

Invalid wenn 1h Close über SL
TPs gerechnet auf Mid Entry 0,2325
Kein Finanzrat
```

Hinweis: Für MarkdownV2 entweder sehr schlicht ohne Sonderformatierung senden oder zentral escapen. Wichtig ist außerdem: Bei Shorts besser **Strength 82** schreiben, nicht einfach **Score 82**, wenn der Rohscore für Short eigentlich ≤25 sein muss.