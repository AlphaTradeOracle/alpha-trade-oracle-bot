# Implementierungsplan

## Phase 1 — Analyse (abgeschlossen)

**Bestandsaufnahme:** Das Repository war zu Projektbeginn leer. Es wurden keine
bestehenden Komponenten überschrieben.

**Identifizierte technische Risiken**

| Risiko | Auswirkung | Maßnahme |
|---|---|---|
| `pandas-ta` ist unmaintained und inkompatibel mit NumPy ≥ 2 | Build bricht, Indikatoren falsch | Eigene Indicator Engine, siehe `ARCHITECTURE.md` §5.1 |
| Look-ahead-Bias im Backtest | Ergebnisse unbrauchbar, gefährlich irreführend | Signalerzeugung nur auf abgeschlossenen Kerzen (`[:i+1]`), Ausführung ab `i+1`; abgesichert durch `tests/test_backtesting.py` |
| Binance Rate Limits (HTTP 429/418) | IP-Bann | Zentraler Rate-Limiter, `Retry-After`-Auswertung, Redis-Cache für OHLCV |
| LLM erfindet Zahlen (Halluzination) | Falsche Kurse in Nachrichten | LLM erhält nur berechnete Werte, gibt validiertes JSON zurück, darf keine Zahlen liefern; alle Kurse werden aus dem Signal-Objekt formatiert, nicht aus der LLM-Antwort |
| Doppelte Signale bei häufigen Scans | Nachrichten-Spam, Telegram-Sperre | Cooldown pro (Symbol, Timeframe) + Ähnlichkeitsschwelle |
| Zeitzonen-Fehler | Falsche Kerzen-Zuordnung | Ausschließlich UTC intern, `app/core/time.py` |
| Float-Rundung bei Preisen | Fehlerhafte R:R-Berechnung | `Decimal` in Persistenz und Risikoberechnung, `float` nur in Indikator-Vektoren |

**Dokumentierte Annahmen**

1. `DEFAULT_QUOTE_ASSET=USDT`, Spot-Märkte auf Binance.
2. „Mindestens zwei Take-Profit-Ziele" wird als drei umgesetzt (TP1/TP2/TP3),
   entsprechend dem Nachrichtenbeispiel im Auftrag.
3. Signalgültigkeit: 4 × Dauer des Setup-Timeframes (1h ⇒ 4 h), konfigurierbar.
4. Mindestscore für automatischen Versand: 65 (`SIGNAL_MIN_SCORE`).
5. Positionsgröße wird rein informativ auf ein Referenzkapital von 10 000 USDT
   bezogen, weil kein Kontostand abgefragt wird.
6. Backtests laufen auf Binance-Klines, die zur Laufzeit geladen werden; es wird
   kein historischer Datensatz mitgeliefert.

## Phase 2 — Grundgerüst

- [x] Projektstruktur, `pyproject.toml`, `Makefile`, `.gitignore`
- [x] Typisierte Konfiguration (`app/core/config.py`, Pydantic Settings v2)
- [x] Strukturiertes Logging mit Correlation-ID und Secret-Redaction
- [x] SQLAlchemy 2 async Engine, Session-Factory, Redis-Client
- [x] Alembic-Setup und Initialmigration
- [x] Dockerfile, `docker-compose.yml` mit Healthchecks und Volumes
- [x] `/health`, `/ready`, `/version`
- [x] pytest-Grundgerüst mit Fixtures

## Phase 3 — Kernfunktion

- [x] `MarketDataProvider`-Protocol + Binance-Implementierung
- [x] Retry, Timeout, Rate-Limit, Lückenerkennung, Normalisierung, Redis-Cache
- [x] Indicator Engine: EMA/SMA, RSI, MACD, Bollinger, ATR, ADX, StochRSI,
      OBV, Volume-MA, ROC, Supertrend, VWAP
- [x] Marktstruktur: Swings, Support/Resistance, Breakouts, HH/HL, LH/LL, Divergenzen
- [x] Multi-Timeframe-Analyse (1d Makro → 4h Bestätigung → 1h Setup → 15m Timing)
- [x] Signal-Engine mit acht gewichteten Kategorien
- [x] Risk Management: ATR-Stops, S/R-Anpassung, R:R-Erzwingung, Positionsgröße
- [x] Persistierung inkl. Score-Komponenten

## Phase 4 — Telegram

- [x] Kommandos: `/start`, `/help`, `/status`, `/analyze`, `/signal`, `/watch`,
      `/unwatch`, `/watchlist`, `/performance`, `/settings`
- [x] Admin: `/admin_status`, `/run_scan`, `/backtest`, `/reload_config`
- [x] Allowlist-Autorisierung, Admin-Trennung
- [x] MarkdownV2-Escaping, Längenlimit-Aufteilung, Risikohinweis
- [x] Watchlist-Persistierung, geplante Scans, Deduplizierung

## Phase 5 — LLM

- [x] `LLMProvider`-Protocol, OpenAI-kompatible Implementierung
- [x] Versionierter Prompt mit expliziten Verboten
- [x] Pydantic-Validierung, ein Korrektur-Retry, regelbasierter Fallback
- [x] Protokollierung von Provider, Modell, Prompt-Version, Tokens, Laufzeit, Fehlern

## Phase 6 — Backtesting

- [x] Look-ahead-freie Simulation, Gebühren, Slippage, Long/Short
- [x] SL/TP-Simulation, Signalablauf
- [x] Kennzahlen: Trefferquote, Profit Factor, Expectancy, Drawdown, Sharpe,
      Sortino, Aufschlüsselung nach Symbol/Timeframe/Richtung
- [x] CLI `python -m app.cli backtest`
- [x] Strategieversionen, Vergleich zweier Versionen, Optimizer-Grundstruktur

## Phase 7 — Qualitätssicherung

- [x] Unit- und Integrationstests der kritischen Geschäftslogik
- [x] Ruff, mypy, pre-commit
- [x] README, Architektur- und Datenmodell-Dokumentation
- [x] Seed-Skript

## Roadmap nach dem MVP

1. Produktive Sentiment-Quellen (Fear & Greed, Funding Rates, Open Interest)
2. Market-Cap Top-1000 Universe (CoinGecko → Exchange-Mapping → Batch-Scan) — umgesetzt
3. Weitere Marktdaten-Provider (Bybit, Kraken) und Multi-Provider-Aggregation
4. Prometheus-Export der bereits vorhandenen Metrik-Struktur
5. Walk-Forward-Optimierung mit manueller Freigabe-Oberfläche
6. Volumenprofil-basierte Support-/Resistance-Erkennung
7. Webhook-Betrieb für Telegram statt Long Polling
8. Signal-Nachverfolgung: automatische Auswertung, ob TP oder SL zuerst erreicht wurde
