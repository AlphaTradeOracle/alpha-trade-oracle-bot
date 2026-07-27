# Signal-Logik

Die Signalerzeugung ist vollständig regelbasiert und deterministisch. Ein LLM ist
zu keinem Zeitpunkt an der Entscheidung beteiligt — es formuliert ausschließlich
die Begründung in natürlicher Sprache.

## 1. Multi-Timeframe-Rollen

| Timeframe | Rolle | Gewicht in der MTF-Kategorie |
|---|---|---|
| `1d` | Makrotrend — bestimmt die übergeordnete Richtung | 0.35 |
| `4h` | Bestätigung der Trendrichtung | 0.30 |
| `1h` | Setup-Erkennung (primärer Timeframe) | 0.25 |
| `15m` | Feinabstimmung des Einstiegs | 0.10 |

Ein Signal entsteht niemals aus einem einzelnen Indikator oder einem einzelnen
Timeframe. Fehlt ein Timeframe (z. B. wegen Datenlücken), wird sein Gewicht auf
die verbleibenden verteilt und die `data_quality` reduziert.

## 2. Kategorien und Gewichtungen

Alle Gewichte liegen zentral in `app/strategies/weights.py` und sind pro
Strategieversion in der Datenbank persistiert. Die Summe muss exakt 1.0 ergeben;
dies wird sowohl vom Pydantic-Modell als auch durch einen Test erzwungen
(`tests/test_signal_weights.py`).

| Kategorie | Standardgewicht | Was gemessen wird |
|---|---|---|
| `trend` | 0.25 | EMA-Stapelung, Preis vs. EMA200, Supertrend-Richtung, ADX-gestützte Trendstärke |
| `momentum` | 0.20 | RSI-Position und -Neigung, MACD-Histogramm, Stochastic RSI, ROC |
| `volume` | 0.15 | Volumen gegen Volume-MA20, OBV-Neigung, Volumenspitzen bei Ausbrüchen |
| `market_structure` | 0.15 | HH/HL bzw. LH/LL, Nähe zu Support/Resistance, bestätigte Breakouts, Fehlausbrüche |
| `multi_timeframe` | 0.15 | Übereinstimmung der Richtungen über alle Timeframes |
| `volatility` | 0.04 | ATR-Prozent im Zielband, Bollinger-Breite (Squeeze/Expansion) |
| `sentiment` | 0.03 | nur wenn `ENABLE_SENTIMENT=true`, sonst neutral (0) und Gewicht umverteilt |
| `risk_reward` | 0.03 | erreichtes R:R gegenüber dem Minimum |

Standardgewichte:

```python
trend_weight            = 0.25
momentum_weight         = 0.20
volume_weight           = 0.15
market_structure_weight = 0.15
multi_timeframe_weight  = 0.15
volatility_weight       = 0.04
sentiment_weight        = 0.03
risk_reward_weight      = 0.03
# Summe = 1.00
```

> **Abweichung vom Auftrag, bewusst getroffen.** Der Auftrag nennt in §9 sieben
> Gewichte, die zusammen 1.0 ergeben, listet Volatilität aber gleichzeitig als
> Score-Bestandteil auf — ohne ihr ein Gewicht zuzuweisen. Ein zusätzliches
> `volatility_weight` musste daher aus dem bestehenden Budget kommen.
>
> Die fünf Hauptkategorien behalten exakt die vorgegebenen Werte
> (0.25 / 0.20 / 0.15 / 0.15 / 0.15 = 0.90). Die verbleibenden 0.10 verteilen
> sich auf `volatility` (0.04), `sentiment` (0.03) und `risk_reward` (0.03) —
> statt der im Auftrag genannten 0.05 für die letzten beiden. Begründung: Diese
> drei Kategorien modifizieren ein Signal, sie erzeugen es nicht. `sentiment`
> ist zudem standardmäßig deaktiviert und wird dann ohnehin umverteilt.
>
> `StrategyWeights` erzwingt die Summe 1.0 über einen Pydantic-Validator, die
> Tabelle `strategy_versions` zusätzlich über einen CHECK-Constraint.

## 3. Vom Rohwert zum Score

Jede Kategorie liefert einen **Rohwert in [−100, +100]**, wobei das Vorzeichen die
Richtung angibt (positiv = bullisch). Der Gesamtwert ist die gewichtete Summe:

```
raw_total = Σ (raw_score_i × weight_i)        →  liegt in [−100, +100]
score     = (raw_total + 100) / 2             →  liegt in [0, 100]
```

Ein `score` von 50 ist damit exakt neutral. Der vollständige Breakdown jeder
Kategorie (Rohwert, Gewicht, gewichteter Beitrag, Begründungstext) wird in
`signal_score_components` gespeichert.

### Beispiel für die Trend-Kategorie

| Bedingung | Beitrag |
|---|---|
| EMA9 > EMA20 > EMA50 (bullische Stapelung) | +30 |
| Preis > EMA200 | +25 |
| Supertrend-Richtung bullisch | +20 |
| ADX > 25 (Trend vorhanden) | Verstärkung um Faktor 1.2 |
| ADX < 20 (kein Trend) | Dämpfung um Faktor 0.6 |

Bärische Konstellationen ergeben die spiegelbildlichen negativen Beiträge. Der
Wert wird auf [−100, +100] begrenzt.

## 4. Richtungsentscheidung

```
score ≥ 80  und  MTF-Übereinstimmung ≥ 0.6   →  STRONG_LONG
score ≥ 65                                   →  LONG
score ≤ 20  und  MTF-Übereinstimmung ≤ −0.6  →  STRONG_SHORT
score ≤ 35                                   →  SHORT
sonst                                        →  NEUTRAL
```

Zusätzlich wird `NO_TRADE` gesetzt, wenn eine der folgenden Bedingungen zutrifft.
Diese Prüfungen überschreiben jede Richtung:

- Das erreichbare R:R liegt unter `MIN_RISK_REWARD_RATIO` (Standard 2.0).
- `data_quality` liegt unter 60 (zu viele fehlende Kerzen oder zu kurze Historie).
- Der ATR-Anteil überschreitet `MAX_ATR_PERCENT` (Standard 12 %) — der Markt ist
  zu volatil für eine sinnvolle Stop-Platzierung.
- Der Stop-Abstand liegt unter `MIN_STOP_DISTANCE_PERCENT` (Standard 0.3 %) — der
  Stop wäre reines Marktrauschen.

## 5. Konfidenz

Die Konfidenz ist absichtlich vom Score getrennt: ein hoher Score bei
widersprüchlichen Timeframes ist weniger belastbar als ein mittlerer Score bei
klarer Übereinstimmung.

```
confidence = f(|score − 50|, MTF-Übereinstimmung, data_quality)

HIGH    : |score−50| ≥ 25  und  |MTF| ≥ 0.6  und  data_quality ≥ 85
MEDIUM  : |score−50| ≥ 12  und  data_quality ≥ 70
LOW     : sonst
```

## 6. Risikomanagement

Berechnet in `app/signals/risk.py`, ausschließlich informativ.

**Entry-Zone.** Um den Referenzkurs herum, halbe ATR-Breite:
`entry_low = price − 0.25 × ATR`, `entry_high = price + 0.25 × ATR` für Long
(spiegelbildlich für Short).

**Stop-Loss.** Basis ist `ATR × ATR_MULTIPLIER` (Standard 1.5) unterhalb der
Entry-Zone. Liegt ein Support innerhalb von `1.0 × ATR` darunter, wird der Stop
knapp darunter gesetzt (Support-Puffer 0.15 %), weil ein Stop unmittelbar
oberhalb eines Supports überproportional oft abgeräumt wird.

Anschließende Prüfungen:
- Stop-Abstand < `MIN_STOP_DISTANCE_PERCENT` ⇒ auf das Minimum aufgeweitet
- Stop-Abstand > `MAX_STOP_DISTANCE_PERCENT` (Standard 8 %) ⇒ Signal wird
  markiert (`wide_stop`) und in den Gegenargumenten vermerkt

**Take-Profit.** Vielfache des Risikoabstands `R = |entry − stop|`:

```
TP1 = entry + 1.5 × R
TP2 = entry + 2.5 × R
TP3 = entry + 4.0 × R
```

Liegt ein Widerstand vor einem TP-Ziel, wird das Ziel knapp darunter gezogen,
damit es realistisch erreichbar bleibt.

**Risiko-Rendite-Verhältnis.** Referenz ist TP2:
`R:R = |TP2 − entry| / |entry − stop|`. Unterschreitet dieser Wert das Minimum,
wird `NO_TRADE` gesetzt.

**Positionsgröße.** Rein informativ, bezogen auf ein Referenzkapital von
10 000 USDT und `MAX_RISK_PERCENT` (Standard 1 %):
`size = (kapital × risiko%) / |entry − stop|`. Es werden keine Orders erzeugt.

## 7. Signalgültigkeit und Invalidierung

**Ablaufzeit:** `expires_at = created_at + 4 × Dauer(primary_timeframe)`.
Bei `1h` also vier Stunden. Abgelaufene Signale werden nie versendet und im
Backtest als `expired` geschlossen.

**Invalidierungsbedingung:** Wird als Klartext im Signal gespeichert, z. B.
„4h-Schlusskurs unter 65.900 USDT". Sie verweist immer auf einen Schlusskurs des
Bestätigungs-Timeframes, nicht auf ein kurzes Durchstechen des Stops.

## 8. Deduplizierung

Zweistufig, implementiert in `app/signals/dedup.py`.

**Stufe 1 — Fingerprint.** SHA-256 über `symbol | primary_timeframe | direction |
round(score/5) | round(entry_mid, Preisgenauigkeit) | strategy_version`. Ein
identischer Fingerprint innerhalb der Cooldown-Zeit gilt als Duplikat.

**Stufe 2 — Cooldown und Relevanzprüfung.** Pro `(Symbol, Timeframe)` gilt
`SIGNAL_COOLDOWN_MINUTES` (Standard 120). Innerhalb dieser Zeit wird nur
versendet, wenn sich das Signal *relevant* geändert hat:

- Richtungswechsel, **oder**
- Score-Änderung ≥ 10 Punkte, **oder**
- Verschiebung der Entry-Mitte ≥ 0.75 × ATR

Der Cooldown liegt in Redis (Key `signal:cooldown:{symbol}:{timeframe}`) mit
Fallback auf eine datenbankbasierte Prüfung des letzten Signals, damit ein
Redis-Ausfall keine Signalflut auslöst.

## 9. Versandbedingungen

Ein Signal wird nur zugestellt, wenn **alle** Bedingungen erfüllt sind:

1. `direction != NEUTRAL` und `direction != NO_TRADE`
2. `score ≥ SIGNAL_MIN_SCORE` (Standard 65) bzw. ≥ Chat-Override
3. `expires_at > now`
4. Keine Duplikat-Erkennung (Stufe 1 und 2)
5. `data_quality ≥ 60`
6. `risk_reward_ratio ≥ MIN_RISK_REWARD_RATIO`

Unterdrückte Signale werden trotzdem persistiert und in `signal_deliveries` mit
`status='suppressed'` und `suppression_reason` protokolliert. Das erhält die
Auswertbarkeit, ohne den Chat zu fluten.

## 10. Rolle des LLM

Das LLM erhält ein JSON-Objekt mit dem fertigen Signal — Richtung, Score,
Kategorie-Breakdown, Indikatorwerte, Kurse. Es liefert validiertes JSON zurück mit
den Feldern `summary`, `reasons`, `risks`, `market_sentiment_note`.

Verbindliche Einschränkungen im Prompt (`app/llm/prompts.py`):

- keine Zahlen ändern oder neue Zahlen erfinden
- keine Gewinnversprechen
- Unsicherheiten benennen
- keine Handlungsanweisung zur Orderausführung

Alle Preise in der Telegram-Nachricht werden **aus dem Signal-Objekt** formatiert,
nicht aus der LLM-Antwort. Selbst eine halluzinierte Zahl in der Zusammenfassung
kann damit keinen falschen Kurs in die Nachricht bringen. Schlägt die Validierung
zweimal fehl, greift der regelbasierte Textbaustein.
