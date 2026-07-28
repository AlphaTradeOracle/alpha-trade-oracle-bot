# Datenmodell

Alle Tabellen liegen im Schema `public` der Datenbank `alpha_trade_oracle`.
Zeitstempel sind `TIMESTAMPTZ` und werden intern immer in UTC geschrieben.
Preise und Beträge nutzen `NUMERIC(24, 8)`, um Float-Rundungsfehler bei
Risiko-Rendite-Berechnungen zu vermeiden.

## Übersicht

```
users ──┬── telegram_chats ──┬── watchlists ──── assets
        │                    └── signal_deliveries
        │                                │
strategies ── strategy_versions ─────────┼──── signals ──┬── signal_score_components
                    │                    │       │       └── llm_requests
                    │                    │       └── indicator_snapshots
                    └── backtest_runs ──┬── backtest_trades
                                        └── backtest_metrics

assets ──── market_candles
model_configs      scheduled_jobs      application_events
```

## Kern-Tabellen

### `assets`
Handelbare Instrumente.

| Spalte | Typ | Bemerkung |
|---|---|---|
| `id` | PK | |
| `symbol` | `VARCHAR(32)` UNIQUE | z. B. `BTCUSDT` |
| `base_asset` | `VARCHAR(16)` | `BTC` |
| `quote_asset` | `VARCHAR(16)` | `USDT` |
| `exchange` | `VARCHAR(32)` | `binance` |
| `price_precision` | `INT` | Dezimalstellen für die Ausgabe |
| `is_active` | `BOOL` | |
| `coingecko_id` | `VARCHAR(64)` | CoinGecko-Slug, nullable |
| `market_cap_rank` | `INT` | aktueller Market-Cap-Rang, nullable |
| `market_cap_usd` | `NUMERIC(28,8)` | Marktkapitalisierung in USD, nullable |
| `in_universe` | `BOOL` | Teil des Top-N-Scan-Universums |
| `last_ranked_at` | `TIMESTAMPTZ` | letzter Universe-Refresh |
| `last_scanned_at` | `TIMESTAMPTZ` | letzter Batch-Scan (Round-Robin) |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | |

Index `ix_assets_universe_scan` auf `(in_universe, last_scanned_at, market_cap_rank)` für Batch-Auswahl.

### `market_candles`
Normalisierte OHLCV-Daten.

| Spalte | Typ | Bemerkung |
|---|---|---|
| `id` | PK | |
| `asset_id` | FK → `assets` | |
| `timeframe` | `VARCHAR(8)` | `15m`, `1h`, `4h`, `1d` |
| `open_time` | `TIMESTAMPTZ` | Beginn der Kerze |
| `close_time` | `TIMESTAMPTZ` | |
| `open`, `high`, `low`, `close` | `NUMERIC(24,8)` | |
| `volume`, `quote_volume` | `NUMERIC(24,8)` | |
| `trade_count` | `INT` | |
| `is_closed` | `BOOL` | Unfertige Kerzen werden nie für Signale verwendet |

Unique Constraint `(asset_id, timeframe, open_time)` — macht den Import idempotent.
Index auf `(asset_id, timeframe, open_time DESC)` für Zeitreihen-Abfragen.

### `indicator_snapshots`
Berechnete Indikatorwerte zum Analysezeitpunkt. Die fachlich wichtigen Kennzahlen
sind eigene Spalten, damit sie abfragbar und auswertbar bleiben; `extra_values`
nimmt lediglich ergänzende Werte auf.

| Spalte | Typ |
|---|---|
| `id` | PK |
| `asset_id` | FK → `assets` |
| `timeframe` | `VARCHAR(8)` |
| `captured_at`, `candle_open_time` | `TIMESTAMPTZ` |
| `close_price` | `NUMERIC(24,8)` |
| `ema_9`, `ema_20`, `ema_50`, `ema_100`, `ema_200` | `NUMERIC(24,8)` |
| `sma_50`, `sma_200` | `NUMERIC(24,8)` |
| `rsi_14` | `NUMERIC(10,4)` |
| `macd`, `macd_signal`, `macd_histogram` | `NUMERIC(24,8)` |
| `bb_upper`, `bb_middle`, `bb_lower`, `bb_width` | `NUMERIC(24,8)` |
| `atr_14`, `atr_percent` | `NUMERIC(24,8)` |
| `adx_14`, `plus_di`, `minus_di` | `NUMERIC(10,4)` |
| `stoch_rsi_k`, `stoch_rsi_d` | `NUMERIC(10,4)` |
| `obv` | `NUMERIC(28,8)` |
| `volume_ma_20`, `volume_ratio` | `NUMERIC(24,8)` |
| `roc_14` | `NUMERIC(12,4)` |
| `supertrend`, `supertrend_direction` | `NUMERIC(24,8)` / `INT` |
| `vwap` | `NUMERIC(24,8)` |
| `trend_direction`, `trend_strength` | `VARCHAR(16)` / `NUMERIC(6,2)` |
| `structure_state` | `VARCHAR(32)` — `HH_HL`, `LH_LL`, `RANGE` |
| `nearest_support`, `nearest_resistance` | `NUMERIC(24,8)` |
| `extra_values` | `JSONB` |

Unique Constraint `(asset_id, timeframe, candle_open_time)`.

### `signals`
Das zentrale Ergebnisobjekt.

| Spalte | Typ | Bemerkung |
|---|---|---|
| `id` | PK | |
| `asset_id` | FK → `assets` | |
| `strategy_version_id` | FK → `strategy_versions` | Reproduzierbarkeit der Gewichte |
| `created_at` | `TIMESTAMPTZ` | |
| `expires_at` | `TIMESTAMPTZ` | Ablaufzeit |
| `direction` | `VARCHAR(16)` | `STRONG_LONG`…`NO_TRADE` |
| `analyzed_timeframes` | `VARCHAR(64)` | `15m,1h,4h,1d` |
| `primary_timeframe` | `VARCHAR(8)` | Setup-Timeframe |
| `market_phase` | `VARCHAR(32)` | `UPTREND`, `DOWNTREND`, `RANGE`, `VOLATILE` |
| `score` | `NUMERIC(6,2)` | 0–100 |
| `confidence` | `VARCHAR(16)` | `LOW`, `MEDIUM`, `HIGH` |
| `reference_price` | `NUMERIC(24,8)` | Kurs bei Erzeugung |
| `entry_low`, `entry_high` | `NUMERIC(24,8)` | Entry-Zone |
| `stop_loss` | `NUMERIC(24,8)` | |
| `take_profit_1..3` | `NUMERIC(24,8)` | |
| `risk_reward_ratio` | `NUMERIC(10,4)` | gegen TP2 |
| `risk_percent`, `suggested_position_size` | `NUMERIC(12,4)` / `NUMERIC(24,8)` | informativ |
| `data_quality` | `NUMERIC(6,2)` | 0–100, aus Lücken und Historienlänge |
| `invalidation_note` | `TEXT` | |
| `reasons`, `counter_arguments` | `JSONB` (Liste) | begründende Texte |
| `indicators_used` | `JSONB` (Liste) | |
| `fingerprint` | `VARCHAR(64)` | Grundlage der Deduplizierung |
| `is_dispatched` | `BOOL` | |

Indizes: `(asset_id, created_at DESC)`, `(direction, created_at DESC)`, `fingerprint`.

### `signal_score_components`
Der Score-Breakdown relational — eine Zeile pro Kategorie.

| Spalte | Typ |
|---|---|
| `id` | PK |
| `signal_id` | FK → `signals` (ON DELETE CASCADE) |
| `category` | `VARCHAR(32)` — `trend`, `momentum`, `volume`, `volatility`, `market_structure`, `multi_timeframe`, `sentiment`, `risk_reward` |
| `raw_score` | `NUMERIC(6,2)` — −100..+100 |
| `weight` | `NUMERIC(6,4)` |
| `weighted_score` | `NUMERIC(8,4)` |
| `detail` | `TEXT` |

Unique Constraint `(signal_id, category)`.

### `signal_deliveries`
Zustellprotokoll je Chat.

| Spalte | Typ |
|---|---|
| `id` | PK |
| `signal_id` | FK → `signals` |
| `telegram_chat_id` | FK → `telegram_chats` |
| `status` | `VARCHAR(16)` — `pending`, `sent`, `failed`, `suppressed` |
| `suppression_reason` | `VARCHAR(64)` — z. B. `cooldown`, `duplicate`, `below_min_score` |
| `message_id` | `BIGINT` |
| `error_message` | `TEXT` |
| `sent_at`, `created_at` | `TIMESTAMPTZ` |

Unique Constraint `(signal_id, telegram_chat_id)` verhindert doppelte Zustellung.

## Benutzer und Watchlist

### `users`
`id`, `external_ref`, `display_name`, `is_admin`, `is_active`, `created_at`.

### `telegram_chats`
`id`, `chat_id` (UNIQUE, `BIGINT`), `chat_type`, `title`, `user_id` (FK, nullable),
`is_admin`, `is_active`, `notifications_enabled`, `min_score_override`, `created_at`.

### `watchlists`
`id`, `telegram_chat_id` (FK), `asset_id` (FK), `timeframes`, `is_active`, `created_at`.
Unique Constraint `(telegram_chat_id, asset_id)`.

## Strategien

### `strategies`
`id`, `name` (UNIQUE), `description`, `is_active`, `created_at`.

### `strategy_versions`
`id`, `strategy_id` (FK), `version` (`INT`), `is_active`, `created_at`,
`activated_at`, `notes`, plus je Kategorie eine Gewichtsspalte
(`trend_weight`, `momentum_weight`, `volume_weight`, `volatility_weight`,
`market_structure_weight`, `multi_timeframe_weight`, `sentiment_weight`,
`risk_reward_weight`) sowie `min_score`, `min_risk_reward_ratio`, `atr_multiplier`.

Unique Constraint `(strategy_id, version)`. Eine Datenbank-Prüfung stellt sicher,
dass die Summe der Gewichte 1.0 (± 1e-6) ergibt; zusätzlich validiert das
Pydantic-Modell dieselbe Bedingung vor dem Schreiben.

## Backtesting

### `backtest_runs`
`id`, `strategy_version_id` (FK), `symbol`, `timeframe`, `start_at`, `end_at`,
`fee_percent`, `slippage_percent`, `initial_capital`, `status`
(`running`/`completed`/`failed`), `error_message`, `created_at`, `finished_at`,
`parameters` (`JSONB`).

### `backtest_trades`
`id`, `backtest_run_id` (FK, CASCADE), `symbol`, `timeframe`, `direction`,
`entry_at`, `entry_price`, `exit_at`, `exit_price`, `exit_reason`
(`take_profit_1..3`, `stop_loss`, `expired`, `end_of_data`), `quantity`,
`gross_pnl`, `fees`, `net_pnl`, `pnl_percent`, `risk_reward_planned`,
`holding_minutes`, `signal_score`.

### `backtest_metrics`
Eine Zeile pro Kennzahl statt einer breiten Tabelle — so lassen sich neue
Kennzahlen ohne Migration ergänzen: `id`, `backtest_run_id` (FK, CASCADE),
`scope` (`overall`, `long`, `short`, `symbol:BTCUSDT`, `timeframe:1h`),
`metric_name`, `metric_value` (`NUMERIC(20,8)`).
Unique Constraint `(backtest_run_id, scope, metric_name)`.

## Betrieb

### `model_configs`
Persistierte Laufzeitkonfiguration für LLM-Nutzung: `id`, `name` (UNIQUE),
`provider`, `model`, `prompt_version`, `temperature`, `max_tokens`, `is_active`,
`parameters` (`JSONB`), `created_at`.

### `llm_requests`
`id`, `signal_id` (FK, nullable), `provider`, `model`, `prompt_version`,
`status` (`success`, `validation_failed`, `retry_success`, `error`),
`prompt_tokens`, `completion_tokens`, `total_tokens`, `duration_ms`,
`validation_error`, `error_message`, `created_at`.

### `scheduled_jobs`
Idempotenz-Anker für Hintergrundjobs: `id`, `job_key` (UNIQUE), `job_type`,
`interval_seconds`, `last_run_at`, `last_success_at`, `next_run_at`,
`last_status`, `last_error`, `run_count`, `is_enabled`.

### `application_events`
Fachliches Audit-Log: `id`, `event_type`, `severity`, `message`,
`correlation_id`, `payload` (`JSONB`), `created_at`.
Index auf `(event_type, created_at DESC)`.

## Migrationen

Die Initialmigration `alembic/versions/0001_initial_schema.py` erstellt alle
Tabellen, Constraints und Indizes. Ausführung über `make migrate` bzw.
`alembic upgrade head`.
