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
    #: Signal-Alerts (Chart + Analyse). False = nur Paper-Trade-Open mit Chart.
    telegram_signal_dispatch: bool = False

    # --- Marktdaten --------------------------------------------------------
    market_data_provider: str = "binance"
    binance_api_key: SecretStr = SecretStr("")
    binance_api_secret: SecretStr = SecretStr("")
    binance_base_url: str = "https://api.binance.com"
    kucoin_base_url: str = "https://api.kucoin.com"
    coinbase_base_url: str = "https://api.coinbase.com/api/v3/brokerage"
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
    signal_require_strong: bool = True
    #: Fuer Shorts: Score darf maximal so hoch sein (Spiegel zu min_score).
    signal_short_max_score: float = 25.0
    signal_cooldown_minutes: int = 120
    signal_expiry_multiplier: int = 24
    signal_rsi_long_max: float = 75.0
    signal_rsi_short_min: float = 25.0
    signal_block_range_market: bool = True
    signal_min_adx: float = 35.0
    atr_multiplier: float = 1.5
    min_stop_distance_percent: float = 0.3
    max_stop_distance_percent: float = 8.0
    max_atr_percent: float = 12.0
    reference_capital: float = 10_000.0

    # --- Scheduler / Daten -------------------------------------------------
    scan_interval_minutes: int = 30
    candle_limit: int = 500
    min_candles_required: int = 210
    universe_size: int = 1000
    #: Pro Scan-Zyklus: Batch-Groesse (sollte >= universe_target_count sein).
    universe_scan_batch_size: int = 300
    universe_refresh_hours: int = 24
    universe_exchanges: str = "kucoin,binance,coinbase"
    universe_ticker_fallback: bool = True
    #: Begrenzt CoinGecko-Ticker-Lookups (Rate-Limits); Mapping laeuft primaer
    #: ueber KuCoin/Binance/Coinbase-Symbol-Listen.
    universe_ticker_fallback_max: int = 150
    #: Top-N handelbare USD*/USDT/USDC-Paare nach MCAP behalten/scannen
    #: (Rank kann dabei >N sein, wenn hoehere Ranks kein Pair haben).
    universe_target_count: int = 300
    #: Optionaler harter Rank-Ceiling (0 = aus; Prefer universe_target_count).
    universe_max_rank: int = 0
    coinbase_quote_assets: str = "USD,USDC,USDT"
    #: Kerzen/Snapshots aelter als diese Tage werden beim Prune entfernt.
    candle_retention_days: int = 365

    # --- Paper-Trading -----------------------------------------------------
    enable_paper_trading: bool = True
    paper_initial_balance: float = 5_000.0
    paper_margin_per_trade: float = 100.0
    paper_leverage: float = 10.0
    paper_fee_percent: float = 0.1
    paper_move_stop_to_breakeven: bool = True
    paper_update_interval_minutes: int = 5
    #: Retest/Pullback-Entry (Arm B): Fill erst in ATR-Zone, sonst Skip.
    paper_retest_entry_enabled: bool = True
    paper_retest_zone_near: float = 0.35
    paper_retest_zone_far: float = 1.0
    paper_retest_pending_multiplier: int = 4
    #: Backtest nutzt dieselbe Retest-Entry-Regel (statt naechster 1h-Open / IST).
    backtest_retest_entry_enabled: bool = True
    #: Paper-Trade-Telegram-Chart: leer = naechst hoeherer TF als Setup (z. B. 1h -> 4h).
    paper_telegram_chart_timeframe: str = ""

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
