# Alpha Trade Oracle Bot

KI-gestützter Telegram-Bot für Krypto-Marktanalysen und strukturierte Trading-Signale.

**Wichtig:** Der Bot führt keine Trades aus, greift nicht auf Wallets zu und gibt keine
Anlageberatung. Jede Telegram-Nachricht enthält einen entsprechenden Hinweis.

## Was der Bot liefert

- Long-, Short- oder Neutral-Signale mit Score (0–100)
- Entry-Zone, Stop-Loss, Take-Profit-Ziele und Chance-Risiko-Verhältnis
- Multi-Timeframe-Analyse (15m, 1h, 4h, 1d)
- Technische Begründung und Gegenargumente
- Optionale LLM-Zusammenfassung (kein Entscheidungsrecht)
- Watchlist, geplante Scans, Deduplizierung
- Backtesting mit denselben Indikatoren und derselben Signal-Logik

## Architektur (kurz)

```
Telegram / FastAPI
        │
   AnalysisService / ScanService / BacktestService
        │
   Indicators → Signal-Engine → Risk → Dedup → (optional LLM)
        │
   Binance (public REST) · PostgreSQL · Redis
```

Details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/SIGNAL_LOGIC.md`](docs/SIGNAL_LOGIC.md),
[`docs/DATA_MODEL.md`](docs/DATA_MODEL.md),
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

### Technische Entscheidung: eigene Indikatoren statt `pandas-ta`

`pandas-ta` ist seit Jahren ohne Release und bricht mit NumPy ≥ 2. Die Indicator Engine
ist deshalb selbst implementiert (`app/indicators/`) und einzeln getestet.

## Voraussetzungen

- Python 3.12+
- Docker und Docker Compose (für den empfohlenen Start)
- Optional: Telegram-Bot-Token von [@BotFather](https://t.me/BotFather)
- Optional: OpenRouter-/OpenAI-kompatibler API-Key für LLM-Zusammenfassungen

## Schnellstart mit Docker

```bash
cp .env.example .env
# TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_IDS und Passwörter setzen
docker compose up --build
```

Danach:

| Dienst | URL / Befehl |
|---|---|
| API | http://localhost:8000 |
| Health | http://localhost:8000/health |
| OpenAPI | http://localhost:8000/docs |
| Worker | Telegram-Bot + Scheduler (separater Container) |

Migration und Seed laufen automatisch über den `migrate`-Service.

## Lokale Installation (ohne Docker)

```bash
python -m venv .venv
# Windows:
.\.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -e ".[dev]"
cp .env.example .env
```

PostgreSQL und Redis müssen erreichbar sein (lokale Instanz oder Docker nur für die
Infrastruktur). Anschließend:

```bash
make migrate
make seed
make dev      # FastAPI auf :8000
make worker   # Telegram + Scheduler
```

## Konfiguration

Alle Einstellungen laufen über Umgebungsvariablen (siehe `.env.example`).

Mindestens setzen:

| Variable | Zweck |
|---|---|
| `POSTGRES_PASSWORD` | Datenbankpasswort |
| `TELEGRAM_BOT_TOKEN` | Bot-Token von BotFather |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Erlaubte Chat-IDs (Kommaliste) |
| `TELEGRAM_ADMIN_CHAT_IDS` | Admin-Chats für `/run_scan`, `/backtest` |
| `ADMIN_API_TOKEN` | Schutz der Admin-API (`X-Admin-Token`) |

Optional:

| Variable | Zweck |
|---|---|
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | LLM-Zusammenfassung |
| `ENABLE_LLM_ANALYSIS` | `true`/`false` — System funktioniert auch ohne LLM |
| `ENABLE_SENTIMENT` | standardmäßig `false` |
| `DEFAULT_SYMBOLS` | Fallback, wenn keine Watchlist existiert |
| `ENABLE_UNIVERSE_SCAN` | Top-N Market-Cap-Batch-Scans (Default `true`) |
| `UNIVERSE_SIZE` / `UNIVERSE_SCAN_BATCH_SIZE` | Größe und Batch des Universums |
| `COINGECKO_API_KEY` | optional — höhere CoinGecko-Limits |

Binance-API-Keys sind für reine Marktanalysen **nicht** erforderlich
(öffentliche REST-Endpunkte).

## Telegram-Bot einrichten

1. Bei [@BotFather](https://t.me/BotFather) `/newbot` ausführen und Token kopieren.
2. Token in `.env` als `TELEGRAM_BOT_TOKEN` eintragen.
3. Dem Bot eine Nachricht schreiben, dann die eigene Chat-ID ermitteln
   (z. B. über `@userinfobot` oder die Worker-Logs).
4. Chat-ID in `TELEGRAM_ALLOWED_CHAT_IDS` eintragen (Admins zusätzlich in
   `TELEGRAM_ADMIN_CHAT_IDS`).
5. Worker starten: `make worker` bzw. `docker compose up worker`.

### Erster Test

Im erlaubten Chat:

```
/start
/analyze BTCUSDT
/watch ETHUSDT
/watchlist
/status
```

Admin:

```
/run_scan
/backtest BTCUSDT
/admin_status
```

## CLI-Beispiele

```bash
python -m app.cli analyze BTCUSDT --no-llm
python -m app.cli universe refresh
python -m app.cli scan --universe --no-dispatch
python -m app.cli scan --symbols BTCUSDT,ETHUSDT
python -m app.cli backtest --symbol BTCUSDT --timeframe 1h --start 2024-01-01 --end 2025-01-01
python -m app.cli check
python -m app.cli seed
```

Makefile-Äquivalente: `make analyze`, `make scan`, `make backtest`, `make check`, `make seed`.

## Top-1000 Market-Cap-Universe

1. `python -m app.cli universe refresh` lädt das CoinGecko-Top-N und mappt auf
   handelbare Paare des aktiven Providers (`MARKET_DATA_PROVIDER`).
2. Der Scheduler refreshed alle `UNIVERSE_REFRESH_HOURS` (Default 24) und scannt
   alle `SCAN_INTERVAL_MINUTES` eine Batch von `UNIVERSE_SCAN_BATCH_SIZE`
   Symbolen (Round-Robin über `last_scanned_at`).
3. Bulk-Scans laufen ohne LLM; Telegram-Versand nur für Symbole auf einer
   aktiven Watchlist.

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/ready` | Readiness (DB, Redis, …) |
| GET | `/version` | Versions- und Feature-Info |
| GET | `/api/v1/assets` | Bekannte Instrumente |
| GET | `/api/v1/signals` | Signale auflisten |
| GET | `/api/v1/signals/{id}` | Signaldetail |
| POST | `/api/v1/analysis` | Ad-hoc-Analyse |
| POST | `/api/v1/backtests` | Backtest (Admin-Token) |
| GET | `/api/v1/backtests/{id}` | Backtest-Ergebnis |
| GET | `/api/v1/performance` | Signalauswertung |

## Tests und Qualität

```bash
make test
make lint
make format
make typecheck
```

Die Suite deckt Indikatoren, Scoring, Risiko, Deduplizierung, Formatierung, LLM-Validierung,
Backtesting (inkl. Look-ahead-Freiheit), Marktdaten, API, Repositories und den
vertikalen Analyse-/Scan-Ablauf ab.

## Sicherheit

- Keine Secrets im Repository (`.env` ist gitignored)
- Admin-Endpunkte und Admin-Bot-Befehle sind getrennt abgesichert
- LLM darf berechnete Zahlen nicht ändern; bei Validierungsfehler Fallback auf Regeltext
- Secrets erscheinen nicht in strukturierten Logs
- Keine Orderausführung, keine Wallet-Anbindung

## Bekannte Einschränkungen (MVP)

- Sentiment-Modul ist vorbereitet, aber standardmäßig deaktiviert
- Automatische Kalibrierung speichert Kandidatenversionen, aktiviert sie aber nie selbst
- Performance-Endpunkt bewertet Signalproduktion, nicht Trade-Outcomes
- Marktdaten-Provider: Binance und KuCoin (Umschalten via `MARKET_DATA_PROVIDER`)
- Top-1000 Market-Cap-Universe via CoinGecko (`universe refresh` + Batch-Scan)
- Docker Desktop muss auf dem Host installiert sein

## Roadmap

- Weitere Marktdaten-Provider (Bybit, Kraken) und Multi-Provider-Aggregation
- Sentiment-Quellen (Fear & Greed, Funding, Dominanz)
- Walk-Forward-Kalibrierung mit manueller Freigabe
- Trade-Outcome-Tracking für echte Signalperformance
- Webhook-Modus für Telegram (statt Long Polling)
- Metriken-Export (Prometheus)

## Lizenz

MIT
