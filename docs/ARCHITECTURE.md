# Alpha Trade Oracle Bot — Architektur

## 1. Zweck und Abgrenzung

Alpha Trade Oracle Bot überwacht Kryptowährungen, berechnet technische Indikatoren über
mehrere Timeframes, erzeugt daraus nachvollziehbare Trading-Signale und versendet diese
formatiert über Telegram.

**Ausdrücklich nicht Bestandteil des Systems:**

- Ausführung von Orders an einer Börse
- Zugriff auf private Wallets, Seed Phrases oder Auszahlungsfunktionen
- Gewinnversprechen jeglicher Art

Jede ausgehende Telegram-Nachricht enthält einen Risikohinweis („Keine Finanzberatung").
Diese Eigenschaft ist durch einen Test abgesichert (`tests/test_formatting.py`).

## 2. Leitprinzipien

| Prinzip | Umsetzung im Projekt |
|---|---|
| Clean Architecture | Domänenlogik (`indicators`, `signals`, `backtesting`) kennt weder Datenbank noch HTTP noch Telegram. |
| Dependency Inversion | Externe Systeme werden über `Protocol`-Interfaces angebunden (`MarketDataProvider`, `LLMProvider`, `SentimentProvider`). |
| Determinismus | Indikator- und Signalberechnung sind reine Funktionen über einem `pandas.DataFrame`. Gleiche Eingabe ⇒ gleiche Ausgabe. Das ist die Voraussetzung dafür, dass Backtest und Live-Betrieb identische Ergebnisse liefern. |
| Nachvollziehbarkeit | Jedes Signal speichert seinen vollständigen Score-Breakdown relational, nicht nur als JSON-Blob. |
| Fail-Soft | LLM, Sentiment und Redis sind optionale Schichten. Fällt eine aus, arbeitet das System regelbasiert weiter. |
| UTC intern | Alle Zeitstempel sind `datetime` mit `tzinfo=UTC`. Konvertierung passiert ausschließlich in der Darstellungsschicht. |

## 3. Schichtenmodell

```
┌──────────────────────────────────────────────────────────────┐
│  Zugangsschicht                                              │
│  app/api (FastAPI)        app/bot (Telegram)                 │
└───────────────────────────┬──────────────────────────────────┘
                            │  ruft Services auf, enthält keine Fachlogik
┌───────────────────────────▼──────────────────────────────────┐
│  Orchestrierung                                              │
│  app/services: AnalysisService, ScanService, BacktestService  │
│  app/scheduler: periodische Scans (APScheduler)               │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  Domäne (rein, ohne I/O)                                     │
│  app/indicators  → Indicator Engine                          │
│  app/signals     → Scoring, Multi-Timeframe, Risk, Dedup     │
│  app/strategies  → Gewichtungen, Strategieversionen          │
│  app/backtesting → Simulation, Kennzahlen                    │
└───────────────────────────┬──────────────────────────────────┘
                            │  über Protocol-Interfaces
┌───────────────────────────▼──────────────────────────────────┐
│  Infrastruktur                                               │
│  app/market_data (Binance)   app/llm (OpenAI-kompatibel)     │
│  app/database (PostgreSQL, Redis)   app/repositories          │
│  app/sentiment   app/monitoring   app/core (Config, Logging)  │
└──────────────────────────────────────────────────────────────┘
```

Die Abhängigkeitsrichtung zeigt immer nach innen. `app/indicators` und `app/signals`
importieren nichts aus `app/database`, `app/api` oder `app/bot`.

## 4. Vertikaler Ablauf einer Analyse

Dies ist der Kernpfad, der im MVP zuerst vollständig funktioniert:

```
/analyze BTCUSDT
   │
   ├─ 1. bot/handlers        Autorisierung der Chat-ID
   ├─ 2. AnalysisService     Orchestrierung
   ├─ 3. MarketDataProvider  OHLCV für 15m, 1h, 4h, 1d (Binance public REST)
   │                         Retry, Timeout, Rate-Limit, optionaler Redis-Cache
   ├─ 4. IndicatorEngine     ~20 Indikatoren + Marktstruktur pro Timeframe
   ├─ 5. TimeframeAnalyzer   je Timeframe: Trend, Momentum, Volumen, Struktur
   ├─ 6. SignalEngine        gewichteter Score 0–100 → Richtung
   ├─ 7. RiskManager         Entry-Zone, ATR-Stop, TP1..TP3, R:R-Prüfung
   ├─ 8. SignalDeduplicator  Cooldown + Ähnlichkeitsprüfung
   ├─ 9. LLMService          optionale Zusammenfassung, validiert, mit Fallback
   ├─10. SignalRepository    Persistierung inkl. Score-Komponenten
   └─11. MessageFormatter    Telegram-Nachricht + Risikohinweis
```

Schritte 3–8 sind synchron und ohne LLM lauffähig. Fällt Schritt 9 aus, greift der
regelbasierte Textbaustein (`app/bot/formatting.py`).

## 5. Technologieentscheidungen und Abweichungen

Der Master-Prompt erlaubt begründete Abweichungen. Folgende wurden getroffen:

### 5.1 Keine `pandas-ta`-Abhängigkeit — Indikatoren in-house

`pandas-ta` ist seit 2021 (0.3.14b) ohne Release, bricht mit NumPy ≥ 2 und
Pandas ≥ 2.2 und ist damit nicht „aktiv gepflegt" im Sinne der Anforderung.
Betrachtete Alternativen:

| Option | Bewertung |
|---|---|
| `TA-Lib` | Schnell und etabliert, benötigt aber eine kompilierte C-Bibliothek. Erschwert `pip install` und Docker-Builds erheblich. |
| `pandas-ta-classic` / Forks | Community-Forks ohne verlässliche Wartungszusage. |
| **Eigene Implementierung** | **Gewählt.** ~450 Zeilen reines pandas/numpy, keine Fremdabhängigkeit, jeder Indikator einzeln testbar (explizite Anforderung), volle Kontrolle über Look-ahead-Freiheit. |

Alle Indikatoren sind gegen bekannte Referenzwerte getestet (`tests/test_indicators.py`).

### 5.2 APScheduler statt Celery

Für das MVP genügen periodische Scans ohne verteilte Task-Queue. APScheduler läuft
im Worker-Prozess, benötigt keinen zusätzlichen Broker-Vertrag und ist über
`ScheduledJobRepository` idempotent abgesichert. Der Wechsel zu Celery bleibt möglich,
weil Jobs als eigenständige Service-Aufrufe formuliert sind (`app/scheduler/jobs.py`).

### 5.3 `structlog` für strukturierte Logs

Erfüllt die Anforderung „strukturierte JSON-Logs" mit Correlation-ID und
Secret-Redaction (`app/core/logging.py`).

### 5.4 Binance ohne API-Key

Für Marktanalysen werden ausschließlich öffentliche REST-Endpunkte genutzt
(`/api/v3/klines`, `/api/v3/ticker/price`, `/api/v3/exchangeInfo`). `BINANCE_API_KEY`
bleibt in der Konfiguration vorhanden, ist aber optional und wird im MVP nicht benötigt.

### 5.5 Asynchroner Stack durchgehend

FastAPI, `httpx.AsyncClient`, SQLAlchemy 2 async (`asyncpg`), `redis.asyncio` und
`python-telegram-bot` ≥ 21 sind alle async. Damit entfällt Thread-Pool-Bridging.
CPU-gebundene Indikatorberechnung läuft synchron innerhalb der Coroutine, da sie
im Millisekundenbereich liegt.

## 6. Prozessmodell

Zwei Rollen aus demselben Image, gesteuert über die Startkommandos:

| Service | Kommando | Aufgabe |
|---|---|---|
| `app` | `uvicorn app.main:app` | REST-API, Healthchecks |
| `worker` | `python -m app.cli worker` | Telegram-Polling + APScheduler-Scans |

Die Trennung verhindert, dass ein Neustart der API laufende Scans abbricht, und
vermeidet doppelte Telegram-Polling-Verbindungen bei mehreren API-Replicas.

## 7. Externe Schnittstellen

| System | Anbindung | Ausfallverhalten |
|---|---|---|
| Binance REST | `httpx`, Retry mit Exponential Backoff + Jitter, Timeout 10 s, Respektieren von HTTP 429/418 inkl. `Retry-After` | Analyse schlägt mit klarer Fehlermeldung fehl; kein Signal wird erzeugt |
| PostgreSQL | SQLAlchemy 2 async, Connection Pool | Readiness-Check schlägt fehl; Analyse ohne Persistierung wird abgelehnt |
| Redis | `redis.asyncio` | Degradiert zu „kein Cache / In-Memory-Cooldown", System bleibt funktionsfähig |
| Telegram | `python-telegram-bot`, Rate-Limit-Warteschlange | Signale werden persistiert, Zustellung als `failed` protokolliert |
| LLM (OpenAI-kompatibel) | `httpx`, ein Korrektur-Retry, dann Fallback | Regelbasierte Nachricht |

## 8. Sicherheit

- Secrets ausschließlich über Umgebungsvariablen, `.env` ist in `.gitignore`.
- `app/core/logging.py` redigiert Felder, deren Name `token`, `key`, `secret`,
  `password` oder `authorization` enthält, bevor sie ins Log gelangen.
- Telegram-Zugriff über Allowlist von Chat-IDs; Admin-Kommandos zusätzlich über
  eine separate Admin-Allowlist.
- Administrative API-Endpunkte (`POST /api/v1/backtests`) erfordern den Header
  `X-Admin-Token`.
- Das LLM erhält ausschließlich bereits berechnete Zahlen, niemals Zugangsdaten.

## 9. Bekannte Grenzen des MVP

- Marktstruktur-Erkennung (Support/Resistance) basiert auf fraktalen Swing-Punkten,
  nicht auf Volumenprofilen.
- Divergenz-Erkennung prüft nur den jüngsten Swing-Vergleich.
- Sentiment-Provider sind als Interface vorhanden, aber ohne produktive Datenquelle
  (`ENABLE_SENTIMENT=false`).
- Die automatische Kalibrierung liefert Strategie-Kandidaten, aktiviert sie aber nie
  selbstständig (`ENABLE_AUTO_CALIBRATION=false`).
