"""Zentrale, typisierte Anwendungskonfiguration."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]


class Settings(BaseSettings):
    """Alle Laufzeitparameter, ausschliesslich aus Umgebungsvariablen."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Anwendung ---------------------------------------------------------
    app_env: Environment = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    app_name: str = "Alpha Trade Oracle Bot"
    app_version: str = "0.1.0"
    admin_api_token: SecretStr = SecretStr("")

    # --- Datenbank ---------------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "alpha_trade_oracle"
    postgres_user: str = "alpha_trade_oracle"
    postgres_password: SecretStr = SecretStr("change-me")

    # --- Redis -------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # --- Telegram ----------------------------------------------------------
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_allowed_chat_ids: str = ""
    telegram_admin_chat_ids: str = ""
    #: Signal-Alerts (Chart + Analyse). True = jedes qualifizierte Signal nach Telegram.
    telegram_signal_dispatch: bool = True

    # --- Marktdaten --------------------------------------------------------
    market_data_provider: str = "binance"
    binance_api_key: SecretStr = SecretStr("")
    binance_api_secret: SecretStr = SecretStr("")
    binance_base_url: str = "https://api.binance.com"
    kucoin_base_url: str = "https://api.kucoin.com"
    coinbase_base_url: str = "https://api.coinbase.com/api/v3/brokerage"
    #: Paper fill / TP / SL / mark prices from perpetual venues (not spot).
    paper_use_perp_prices: bool = True
    paper_perp_venues: str = "binance,kucoin,aster,hyperliquid"
    binance_futures_base_url: str = "https://fapi.binance.com"
    kucoin_futures_base_url: str = "https://api-futures.kucoin.com"
    aster_futures_base_url: str = "https://fapi.asterdex.com"
    hyperliquid_base_url: str = "https://api.hyperliquid.xyz"
    coingecko_api_key: SecretStr = SecretStr("")
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    coinmarketcap_api_key: SecretStr = SecretStr("")

    # --- LLM ---------------------------------------------------------------
    llm_provider: str = "openrouter"
    llm_model: str = "openai/gpt-4o-mini"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 900
    llm_timeout_seconds: float = 45.0

    news_api_key: SecretStr = SecretStr("")

    # --- Analyse-Defaults --------------------------------------------------
    default_quote_asset: str = "USDT"
    default_timeframes: str = "15m,1h,4h,1d"
    default_symbols: str = "BTCUSDT,ETHUSDT"
    primary_timeframe: str = "1h"
    max_risk_percent: float = 1.0
    min_risk_reward_ratio: float = 2.0

    # --- Feature-Schalter --------------------------------------------------
    enable_sentiment: bool = False
    enable_llm_analysis: bool = True
    enable_backtesting: bool = True
    enable_auto_calibration: bool = False
    enable_scheduler: bool = True
    enable_universe_scan: bool = True

    # --- Signal- und Risikoparameter --------------------------------------
    signal_min_score: float = 75.0
    #: false = LONG/SHORT ab Score-Schwelle erlaubt (Frequenz-Test 2026-07; ADX/Score bleiben).
    signal_require_strong: bool = False
    #: Fuer Shorts: Score darf maximal so hoch sein (Spiegel zu min_score).
    signal_short_max_score: float = 30.0
    signal_cooldown_minutes: int = 120
    signal_expiry_multiplier: int = 24
    signal_rsi_long_max: float = 75.0
    signal_rsi_short_min: float = 33.0
    #: Shorts mit Score <= diesem Wert gelten als ueberverkauft/Erschoepfung (NO_TRADE).
    signal_short_min_score: float = 18.0
    signal_block_range_market: bool = True
    signal_min_adx: float = 30.0
    #: Soft ADX floor for high-conviction scores (long ≥ min_score / short ≤ short_max).
    #: Lets strong setups through mild chop without disabling the hard ADX gate entirely.
    signal_min_adx_soft: float = 20.0
    atr_multiplier: float = 1.5
    min_stop_distance_percent: float = 0.3
    max_stop_distance_percent: float = 8.0
    #: true = Setups jenseits von ``max_stop_distance_percent`` werden verworfen.
    #: false = sie bleiben handelbar, das risikonormierte Sizing verkleinert die
    #: Position stattdessen automatisch (Default, weil ein Reject die Signalmenge
    #: aendert und damit eine Strategieentscheidung waere).
    reject_wide_stops: bool = False
    max_atr_percent: float = 12.0
    reference_capital: float = 10_000.0
    #: Take-Profit-Leiter als R-Multiples (kommasepariert, genau drei Werte).
    tp_multipliers: str = "1.5,2.5,4.0"
    #: Scale-out-Anteile je TP (Summe 1.0). Default 50/25/25 — mehr Gewinn bei TP1.
    paper_scale_out_fractions: str = "0.5,0.25,0.25"
    #: Nach N aufeinanderfolgenden Verlusten auf demselben Symbol pausieren (0 = aus).
    paper_symbol_circuit_breaker_losses: int = 2
    #: Pausendauer nach Symbol-Circuit-Breaker in Stunden.
    paper_symbol_circuit_breaker_hours: int = 24
    #: UTC-Blackout fuer neue Signale/Paper-Entries (``HH:MM-HH:MM``). Leer = aus.
    #: Leer = aus. Frueher 21:00-01:00 UTC; abgeschaltet, wenn alle anderen Gates greifen.
    signal_entry_blackout_utc: str = ""
    paper_entry_blackout_utc: str = ""
    #: Nach TP1: Expiry auf N x Primary-TF verlaengern (0 = unveraendert 24h ab Fill).
    paper_expiry_multiplier_after_tp1: int = 48
    #: BTC-Regime-Filter: Shorts in Bull-Regime / Longs in Bear-Regime blockieren.
    regime_filter_enabled: bool = True
    regime_btc_symbol: str = "BTCUSDT"
    regime_timeframe: str = "4h"
    #: Global Market Regime Filter (MTF BTC + aux modules + score blend).
    market_regime_enabled: bool = True
    market_regime_hard_veto: bool = True
    market_regime_btc_timeframes: str = "1h,4h,1d,1w"
    market_regime_eth_enabled: bool = True
    market_regime_eth_symbol: str = "ETHUSDT"
    market_regime_funding_enabled: bool = True
    market_regime_fear_greed_enabled: bool = True
    market_regime_dominance_enabled: bool = True
    market_regime_oi_enabled: bool = True
    market_regime_liquidations_enabled: bool = True
    #: Free liquidity venues (comma-separated): binance,bybit,hyperliquid.
    market_regime_liquidity_venues: str = "binance,bybit,hyperliquid"
    market_score_weight_coin: float = 0.60
    market_score_weight_global: float = 0.25
    market_score_weight_funding: float = 0.05
    market_score_weight_oi: float = 0.05
    market_score_weight_liquidations: float = 0.05
    #: Optional paid JSON feed later (CoinGlass/Hyblock/…). Empty = free venues only.
    liquidation_api_url: str = ""

    # --- Institutional Knowledge Base (Parts 1–9) ---------------------------
    #: Explainability + market intel always on; hard no-trade mutations off by default
    #: (soft blend = regime score blend + SIGNAL_SHORT_MAX_SCORE).
    institutional_kb_enabled: bool = True
    institutional_enforce_gates: bool = False
    institutional_min_confidence_pct: float = 55.0
    institutional_min_data_quality: float = 70.0
    institutional_require_positive_ev: bool = False
    institutional_reject_thin_liquidity: bool = False

    # --- Scheduler / Daten -------------------------------------------------
    scan_interval_minutes: int = 15
    #: Parallel symbol workers per market scan (1 = sequential). Speeds 15m cadence.
    scan_concurrency: int = 10
    candle_limit: int = 500
    min_candles_required: int = 210
    universe_size: int = 1500
    #: Pro Scan-Zyklus: Batch-Groesse (sollte >= universe_target_count sein).
    universe_scan_batch_size: int = 400
    universe_refresh_hours: int = 24
    universe_exchanges: str = "kucoin,binance,coinbase"
    universe_ticker_fallback: bool = True
    #: Begrenzt CoinGecko-Ticker-Lookups (Rate-Limits); Mapping laeuft primaer
    #: ueber KuCoin/Binance/Coinbase-Symbol-Listen.
    universe_ticker_fallback_max: int = 150
    #: Top-N handelbare USD*/USDT/USDC-Paare nach MCAP behalten/scannen
    #: (Rank kann dabei >N sein, wenn hoehere Ranks kein Pair haben).
    universe_target_count: int = 400
    #: Nur Bases aufnehmen, die auf mind. einer Perp/Futures-Boerse handelbar sind.
    universe_require_leverage: bool = True
    #: Venues fuer den Leverage-Check (Komma-getrennt).
    universe_leverage_venues: str = "binance,kucoin,aster,hyperliquid"
    #: Optionaler harter Rank-Ceiling (0 = aus; Prefer universe_target_count).
    universe_max_rank: int = 0
    #: Vor der Aufnahme pruefen, ob der Provider fuer das Paar ueberhaupt Kerzen
    #: liefert. Ohne diese Pruefung landen Symbole im Universe, die spaeter bei
    #: jedem Retest-Fill als ``skipped_no_history`` verpuffen.
    universe_verify_candles: bool = True
    #: Mindest-24h-Quotevolumen aus der Verifikationsabfrage (0 = aus).
    #: Default aus, weil das Volumen der *gemappten Boerse* gemessen wird und
    #: KuCoin-Spot duenn ist: bei 1 Mio USD fielen 462 von 516 Paaren raus,
    #: darunter THETA, IMX und PEPE. Ein Wert > 0 veraendert das Universe und
    #: gehoert damit hinter einen Vergleichstest.
    universe_min_quote_volume_usd: float = 0.0
    coinbase_quote_assets: str = "USD,USDC,USDT"
    #: Kerzen/Snapshots aelter als diese Tage werden beim Prune entfernt. Muss
    #: ueber der tiefsten nachgeladenen Historie liegen, sonst macht der Prune
    #: einen Backfill wieder zunichte.
    candle_retention_days: int = 730

    # --- Paper-Trading -----------------------------------------------------
    enable_paper_trading: bool = True
    paper_initial_balance: float = 5_000.0
    paper_margin_per_trade: float = 300.0
    paper_leverage: float = 10.0
    #: 0 = fixe Margin x Hebel (jedes Paper-Trade gleiche Margin, Default $300).
    #: >0 = Stueckzahl aus Risikobetrag und Stop-Abstand (1R-Sizing).
    paper_risk_per_trade_usd: float = 0.0
    #: Obergrenze fuer das Nominal, damit sehr enge Stops keinen absurden Hebel
    #: erzeugen (0 = keine Grenze). Bei fixer Margin meist margin*leverage.
    paper_max_notional_usd: float = 3_000.0
    #: Portfolio-Cap: Summe des offenen Restrisikos in Prozent des Equity.
    paper_max_portfolio_risk_pct: float = 30.0
    #: Harte Obergrenze fuer gleichzeitig offene Positionen (0 = keine Grenze).
    paper_max_open_positions: int = 20
    #: Obergrenze je Richtung. Altcoin-Shorts korrelieren mit ~0.85, neun davon
    #: in einer Stunde sind effektiv eine Wette in neunfacher Groesse.
    paper_max_open_per_direction: int = 12
    #: Taker-Gebuehr je Seite in Prozent. Perpetual-Taker liegt bei 0.045-0.05%;
    #: 0.1% waere ein Spot-Satz und wuerde die Kosten verdoppeln.
    paper_fee_percent: float = 0.05
    paper_move_stop_to_breakeven: bool = True
    paper_update_interval_minutes: int = 5
    #: Stuendlicher Paper-Performance-Digest an TELEGRAM_ALLOWED_CHAT_IDS.
    #: Hourly Telegram digest off by default — desk website is the status surface.
    paper_hourly_digest_enabled: bool = False
    paper_digest_interval_minutes: int = 60
    #: Retest/Pullback-Entry (Arm B): Fill erst in ATR-Zone, sonst Skip.
    paper_retest_entry_enabled: bool = True
    paper_retest_zone_near: float = 0.55
    paper_retest_zone_far: float = 1.0
    paper_retest_pending_multiplier: int = 6
    #: Mindestanzahl aufeinanderfolgender Kerzen in der Retest-Zone vor Fill.
    paper_retest_min_bars_in_zone: int = 1
    #: Backtest nutzt dieselbe Retest-Entry-Regel (statt naechster 1h-Open / IST).
    backtest_retest_entry_enabled: bool = True
    #: Paper-Trade-Telegram-Chart (Default 4h, wie Signal-Charts).
    paper_telegram_chart_timeframe: str = "4h"
    #: Early-Scratch: Position schliessen wenn MFE < Schwelle nach N Stunden (0 = aus).
    paper_early_scratch_hours: int = 12
    paper_early_scratch_mfe_r: float = 0.5

    # --- HTTP --------------------------------------------------------------
    http_timeout_seconds: float = 10.0
    http_max_retries: int = 3
    market_data_cache_ttl_seconds: int = 60

    # --- Ausgabe -----------------------------------------------------------
    display_timezone: str = "Europe/Berlin"

    # --- Validierung -------------------------------------------------------
    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL muss einer von {sorted(allowed)} sein, war: {value!r}")
        return upper

    @field_validator("min_risk_reward_ratio")
    @classmethod
    def _validate_rr(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("MIN_RISK_REWARD_RATIO muss groesser als 0 sein")
        return value

    @field_validator("max_risk_percent")
    @classmethod
    def _validate_risk(cls, value: float) -> float:
        if not 0 < value <= 100:
            raise ValueError("MAX_RISK_PERCENT muss zwischen 0 und 100 liegen")
        return value

    # --- Abgeleitete Werte -------------------------------------------------
    @property
    def database_url(self) -> str:
        """Async-DSN fuer SQLAlchemy/asyncpg."""
        pwd = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{pwd}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """Sync-DSN, wird von Alembic benoetigt."""
        pwd = self.postgres_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.postgres_user}:{pwd}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def timeframes(self) -> list[str]:
        return [tf.strip() for tf in self.default_timeframes.split(",") if tf.strip()]

    @property
    def symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.default_symbols.split(",") if s.strip()]

    @property
    def allowed_chat_ids(self) -> set[int]:
        return _parse_int_set(self.telegram_allowed_chat_ids)

    @property
    def admin_chat_ids(self) -> set[int]:
        return _parse_int_set(self.telegram_admin_chat_ids)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token.get_secret_value())

    @property
    def llm_configured(self) -> bool:
        return self.enable_llm_analysis and bool(self.llm_api_key.get_secret_value())

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def parsed_tp_multipliers(self) -> tuple[float, float, float]:
        parts = [part.strip() for part in self.tp_multipliers.split(",") if part.strip()]
        if len(parts) != 3:
            raise ValueError(
                "TP_MULTIPLIERS muss genau drei kommaseparierte Werte haben, "
                f"war: {self.tp_multipliers!r}"
            )
        return (float(parts[0]), float(parts[1]), float(parts[2]))

    @property
    def parsed_scale_out_fractions(self) -> tuple[float, float, float]:
        parts = [
            part.strip() for part in self.paper_scale_out_fractions.split(",") if part.strip()
        ]
        if len(parts) != 3:
            raise ValueError(
                "PAPER_SCALE_OUT_FRACTIONS muss genau drei Werte haben, "
                f"war: {self.paper_scale_out_fractions!r}"
            )
        values = tuple(float(part) for part in parts)
        total = sum(values)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"PAPER_SCALE_OUT_FRACTIONS muss sich zu 1.0 summieren, war {total:.6f}"
            )
        return values  # type: ignore[return-value]


def _parse_int_set(raw: str) -> set[int]:
    """Kommaseparierte Chat-IDs robust einlesen; ungueltige Werte werden verworfen."""
    result: set[int] = set()
    for part in raw.split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        try:
            result.add(int(cleaned))
        except ValueError:
            continue
    return result


@lru_cache
def get_settings() -> Settings:
    """Prozessweit gecachte Settings-Instanz."""
    return Settings()
