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
die verbleibenden verteilt. Die `data_quality` wird aus den **verfügbaren**
Timeframes gemittelt (ohne Strafe für fehlende höhere TFs bei jungen Listings),
setzt aber voraus: Setup-TF (`1h`) **plus** mindestens ein höherer TF (`4h`/`1d`).

## 2. Kategorien und Gewichtungen

Alle Gewichte liegen zentral in `app/strategies/weights.py` und sind pro
Strategieversion in der Datenbank persistiert. Die Summe muss exakt 1.0 ergeben;
dies wird sowohl vom Pydantic-Modell als auch durch einen Test erzwungen
(`tests/test_signal_weights.py`).

| Kategorie | Standardgewicht (v2) | v1 (Baseline) | Was gemessen wird |
|---|---|---|---|
| `trend` | **0.273** | 0.258* | EMA-Stapelung, Preis vs. EMA200, Supertrend-Richtung, ADX-gestützte Trendstärke |
| `momentum` | 0.218 | 0.206* | RSI-Position und -Neigung, MACD-Histogramm, Stochastic RSI, ROC |
| `volume` | 0.164 | 0.155* | Volumen gegen Volume-MA20, OBV-Neigung, Volumenspitzen bei Ausbrüchen |
| `market_structure` | 0.164 | 0.155* | HH/HL bzw. LH/LL, Nähe zu Support/Resistance, bestätigte Breakouts, Fehlausbrüche |
| `multi_timeframe` | **0.105** | 0.155* | Übereinstimmung der Richtungen über alle Timeframes |
| `volatility` | 0.044 | 0.041* | ATR-Prozent im Zielband, Bollinger-Breite (Squeeze/Expansion) |
| `sentiment` | 0.00 | 0.00* | nur wenn `ENABLE_SENTIMENT=true`, sonst neutral (0) |
| `risk_reward` | 0.033 | 0.031* | erreichtes R:R gegenüber dem Minimum |

\* v1 effektive Werte mit `ENABLE_SENTIMENT=false` (Gewichtsredistribution aus
0.25/0.20/0.15/0.15/0.15 + 0.04/0.03/0.03).

Standardgewichte (v2, Paper-Forward ab 2026-07-30):

```python
# Simulation: reduce_multi_timeframe (+99 USD PnL vs Baseline auf Paper-Sample)
trend_weight            = 0.273   # +1.5pp vs effektive Baseline
momentum_weight         = 0.2184
volume_weight           = 0.1638  # unveraendert relativ zur Baseline
market_structure_weight = 0.1638  # unveraendert relativ zur Baseline
multi_timeframe_weight  = 0.1046  # -5pp vs effektive Baseline
volatility_weight       = 0.0437
sentiment_weight        = 0.0
risk_reward_weight      = 0.0327
# Summe = 1.00
```

Aktivierung auf dem VPS: `python scripts/activate_strategy_weights.py` nach Deploy.
Neue Versionen werden in `strategy_versions` persistiert; der Worker laedt die
aktive Version bei jedem Scan.

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
(spiegelbildlich für Short). Die Zone ist die *Signal*-Referenz; der tatsächliche
Paper-/Backtest-Fill erfolgt bei aktivem Retest per Pullback-These (Abschnitt 6b);
sonst sofort am Signal-Entry (IST).

**Stop-Loss.** Basis ist `ATR × ATR_MULTIPLIER` (Standard 1.5) unterhalb der
Entry-Zone. Liegt ein Support innerhalb von `1.0 × ATR` darunter, wird der Stop
knapp darunter gesetzt (Support-Puffer 0.15 %), weil ein Stop unmittelbar
oberhalb eines Supports überproportional oft abgeräumt wird.

Anschließende Prüfungen:
- Stop-Abstand < `MIN_STOP_DISTANCE_PERCENT` ⇒ auf das Minimum aufgeweitet
- Stop-Abstand > `MAX_STOP_DISTANCE_PERCENT` (Standard 8 %) ⇒ Signal wird
  markiert (`wide_stop`) und in den Gegenargumenten vermerkt

**Take-Profit.** Vielfache des Risikoabstands `R = |entry − stop|` (Default **2/4/6R**,
konfigurierbar via `TP_MULTIPLIERS`; Scale-out Default **50/25/25**):

```
TP1 = entry + 1 × R
TP2 = entry + 2 × R
TP3 = entry + 3 × R
```

Paper-Scale-out Default **50/25/25** (`PAPER_SCALE_OUT_FRACTIONS`) — mehr Gewinn
wird bei TP1 gesichert; Break-Even nach TP1 bleibt aktiv.

Liegt ein Widerstand vor einem TP-Ziel, wird das Ziel knapp darunter gezogen,
damit es realistisch erreichbar bleibt.

**Risiko-Rendite-Verhältnis.** Referenz ist TP2:
`R:R = |TP2 − entry| / |entry − stop|`. Unterschreitet dieser Wert das Minimum,
wird `NO_TRADE` gesetzt. Seit 2026-07 fließt R:R **nicht** mehr in den Score ein
(nur Gate — das frühere direction-blinde Scoring mit 3,27 % Gewicht war fehleranfällig).

**Positionsgröße.** Rein informativ, bezogen auf ein Referenzkapital von
10 000 USDT und `MAX_RISK_PERCENT` (Standard 1 %):
`size = (kapital × risiko%) / |entry − stop|`. Es werden keine Orders erzeugt.

## 6b. Retest / Pullback-Entry (Paper + Backtest, Default ON)

Kanonische Regeln in `app/signals/retest_entry.py`. Mit **24h-Expiry** bleibt Retest
für Entry-Qualität aktiv (Pending 4× TF); nach Fill gilt Hold/Expiry = 24h ab Signal.
Ohne Retest (IST) steigen SL-Treffer im Paper deutlich — Retest-Skip bleibt sinnvoll.

| Env | Default | Wirkung |
|---|---|---|
| `PAPER_RETEST_ENTRY_ENABLED` | `true` | Paper wartet auf ATR-Pullback-Fill |
| `BACKTEST_RETEST_ENTRY_ENABLED` | `true` | Backtest nutzt Retest statt IST |
| `PAPER_RETEST_ZONE_NEAR` | `0.35` | Zone-Innenkante × ATR |
| `PAPER_RETEST_ZONE_FAR` | `1.0` | Zone-Außenkante × ATR |
| `PAPER_RETEST_PENDING_MULTIPLIER` | `4` | Pending-Fenster = N × Primary-TF |

Ablauf:

1. Signal entsteht wie bisher (Score, Gates, Entry-Zone, Signal-SL).
2. Paper legt `status=pending` an (kein Cash-Lock). Pending blockiert das Symbol.
3. Long: Fill wenn eine Folgekerze die Zone `[entry − 1.0×ATR, entry − 0.35×ATR]`
   berührt. Short spiegelbildlich darüber.
4. Fill-Preis = Zonenmitte. Stop = Fill ± ursprüngliches R (Abstand Signal-Entry↔SL).
   TPs neu aus konfigurierter Leiter (Default 2/4/6R). Management-Expiry am Signal-Fenster (`SIGNAL_EXPIRY_MULTIPLIER` × TF); nach TP1 optional 48h (`PAPER_EXPIRY_MULTIPLIER_AFTER_TP1`).
5. Skip ohne Fill, wenn vor dem Retest der Signal-SL getroffen wird, das Pending-
   Fenster abläuft oder keine ATR-Historie verfügbar ist (`exit_reason=retest_skipped`).

Nicht aktiv: Delay+30m und HTF-4h-Breakout (bewusst verworfen / reverted).

## 6c. Paper-Trade-Gates und Telegram

**Live-Scan:** Ein Paper-Trade wird in `scan_service` geöffnet, sobald das Signal
alle Dedup-/Versand-Gates passiert (`should_send=true`) — unabhängig davon, ob
Telegram das Signal zugestellt hat. Zusätzlich: kein aktives Paper pro Symbol,
`enable_paper_trading=true`, Risk-Levels vorhanden, ggf. ausreichend Cash (IST).

**Backfill/Rebuild:** `_passes_paper_gates` in `paper_trading_service.py` (ohne Cooldown):

| Gate | Standard |
|---|---|
| Richtung | actionable (STRONG_LONG/SHORT bei `SIGNAL_REQUIRE_STRONG=true`) |
| Long-Score | ≥ `SIGNAL_MIN_SCORE` (75) |
| Short-Score | ≤ `SIGNAL_SHORT_MAX_SCORE` (25) |
| Datenqualität | ≥ 60 |
| Chance-Risiko (TP2) | ≥ `MIN_RISK_REWARD_RATIO` (2.0) |
| Levels | SL + TP1/2/3 gesetzt |

ADX (`SIGNAL_MIN_ADX=20`) wird bei der Signal-Generierung geprüft, nicht erneut im Paper-Gate.

**Verhaltens-Guards (Paper/Scan):**

| Env | Default | Wirkung |
|---|---|---|
| `PAPER_SYMBOL_CIRCUIT_BREAKER_LOSSES` | `2` | Nach N Verlusten in Folge auf dem Symbol pausieren |
| `PAPER_SYMBOL_CIRCUIT_BREAKER_HOURS` | `24` | Pausendauer in Stunden |
| `SIGNAL_ENTRY_BLACKOUT_UTC` | `21:00-01:00` | Keine neuen Signale/Paper-Entries in diesem UTC-Fenster |
| `PAPER_ENTRY_BLACKOUT_UTC` | `21:00-01:00` | Backfill-Gate für Paper (leer = aus) |

**Telegram:** Standard (`TELEGRAM_SIGNAL_DISPATCH=false`) gehen **nur** Paper-Trade-
Meldungen an alle `TELEGRAM_ALLOWED_CHAT_IDS`: Eroeffnung (IST oder Retest-Fill) und
vollständiger Schluss. Klassische Signal-Alerts (Chart + Analyse) sind deaktiviert.
Mit `TELEGRAM_SIGNAL_DISPATCH=true` zusaetzlich wieder Signal-Dispatch moeglich.
Backfill/Rebuild unterdrückt Paper-Benachrichtigungen.

## 7. Signalgültigkeit und Invalidierung

**Ablaufzeit:** `expires_at = created_at + SIGNAL_EXPIRY_MULTIPLIER × Dauer(primary_timeframe)`.
Standard `SIGNAL_EXPIRY_MULTIPLIER=24`; bei `1h` also **24 Stunden**. Abgelaufene Signale werden nie versendet und im
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

Ein Signal wird nur **verarbeitet** (Paper-Trade + ggf. Dedup), wenn **alle**
Bedingungen erfüllt sind. Telegram-Signal-Alerts nur zusaetzlich bei
`TELEGRAM_SIGNAL_DISPATCH=true`:

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
