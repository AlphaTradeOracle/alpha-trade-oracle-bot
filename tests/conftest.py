"""Gemeinsame Fixtures.

Alle Marktdaten sind synthetisch und deterministisch (fester Seed). Kein Test
ruft eine externe API auf — sonst waeren die Tests von Binance-Verfuegbarkeit und
Netzwerklatenz abhaengig.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Muss vor dem ersten Settings-Import gesetzt sein, damit die Validierung nicht
# eine fehlende Chat-Freigabe moniert.
os.environ.setdefault("TELEGRAM_ALLOWED_CHAT_IDS", "111,222")
os.environ.setdefault("TELEGRAM_ADMIN_CHAT_IDS", "111")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("ENABLE_LLM_ANALYSIS", "false")
os.environ.setdefault("ENABLE_SENTIMENT", "false")
os.environ.setdefault("ADMIN_API_TOKEN", "test-admin-token")
# Market-regime aux sources talk to the public internet — keep them off in tests.
os.environ.setdefault("MARKET_REGIME_FUNDING_ENABLED", "false")
os.environ.setdefault("MARKET_REGIME_FEAR_GREED_ENABLED", "false")
os.environ.setdefault("MARKET_REGIME_DOMINANCE_ENABLED", "false")
os.environ.setdefault("MARKET_REGIME_OI_ENABLED", "false")
os.environ.setdefault("MARKET_REGIME_LIQUIDATIONS_ENABLED", "false")
os.environ.setdefault("MARKET_REGIME_ETH_ENABLED", "false")
# Explainability on; hard no-trade mutations off unless a test opts in.
os.environ.setdefault("INSTITUTIONAL_ENFORCE_GATES", "false")

from app.core.config import Settings, get_settings
from app.database.base import Base
from app.indicators.engine import IndicatorEngine, IndicatorSet
from app.models import *  # noqa: F403 - registriert alle Tabellen an Base.metadata

get_settings.cache_clear()

RANDOM_SEED = 20240101
BASE_TIME = datetime(2024, 1, 1, tzinfo=UTC)
CANDLE_COUNT = 400


def _build_ohlcv(
    *,
    trend_per_candle: float,
    start_price: float = 40_000.0,
    noise: float = 0.0018,
    volume_factor: float = 1.0,
    count: int = CANDLE_COUNT,
    interval_minutes: int = 60,
    seed: int = RANDOM_SEED,
) -> pd.DataFrame:
    """OHLCV-Reihe mit definiertem Drift erzeugen.

    ``trend_per_candle`` ist die relative Veraenderung je Kerze; 0.0 ergibt einen
    Seitwaertsmarkt. Der feste Seed macht jeden Test reproduzierbar.
    """
    rng = np.random.default_rng(seed)

    shocks = rng.normal(loc=trend_per_candle, scale=noise, size=count)
    closes = start_price * np.cumprod(1.0 + shocks)

    # Intrabar-Spanne aus dem Rauschen ableiten, damit High/Low konsistent sind.
    spread = np.abs(rng.normal(loc=noise * 1.4, scale=noise * 0.4, size=count))
    opens = np.concatenate(([start_price], closes[:-1]))
    highs = np.maximum(opens, closes) * (1.0 + spread)
    lows = np.minimum(opens, closes) * (1.0 - spread)

    volumes = np.abs(rng.normal(loc=1_000.0 * volume_factor, scale=120.0, size=count))

    index = pd.DatetimeIndex(
        [BASE_TIME + timedelta(minutes=interval_minutes * i) for i in range(count)],
        name="open_time",
    )
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Settings fuer Tests. Nutzt die Umgebungsvariablen aus diesem Modul."""
    return Settings()


@pytest.fixture(scope="session")
def uptrend_df() -> pd.DataFrame:
    """Klarer Aufwaertstrend."""
    return _build_ohlcv(trend_per_candle=0.0022)


@pytest.fixture(scope="session")
def downtrend_df() -> pd.DataFrame:
    """Klarer Abwaertstrend."""
    return _build_ohlcv(trend_per_candle=-0.0022, seed=RANDOM_SEED + 1)


@pytest.fixture(scope="session")
def sideways_df() -> pd.DataFrame:
    """Seitwaertsmarkt ohne Drift."""
    return _build_ohlcv(trend_per_candle=0.0, seed=RANDOM_SEED + 2)


@pytest.fixture(scope="session")
def indicator_engine() -> IndicatorEngine:
    return IndicatorEngine()


@pytest.fixture(scope="session")
def uptrend_indicators(
    indicator_engine: IndicatorEngine, uptrend_df: pd.DataFrame
) -> dict[str, IndicatorSet]:
    """Indikatorsaetze aller vier Timeframes fuer einen Aufwaertstrend."""
    return {
        tf: indicator_engine.compute(uptrend_df, tf, symbol="BTCUSDT")
        for tf in ("15m", "1h", "4h", "1d")
    }


@pytest.fixture(scope="session")
def downtrend_indicators(
    indicator_engine: IndicatorEngine, downtrend_df: pd.DataFrame
) -> dict[str, IndicatorSet]:
    return {
        tf: indicator_engine.compute(downtrend_df, tf, symbol="BTCUSDT")
        for tf in ("15m", "1h", "4h", "1d")
    }


@pytest.fixture(scope="session")
def sideways_indicators(
    indicator_engine: IndicatorEngine, sideways_df: pd.DataFrame
) -> dict[str, IndicatorSet]:
    return {
        tf: indicator_engine.compute(sideways_df, tf, symbol="BTCUSDT")
        for tf in ("15m", "1h", "4h", "1d")
    }


@pytest.fixture
def build_ohlcv():  # type: ignore[no-untyped-def]
    """Fabrik, damit Tests eigene Reihen mit abweichenden Parametern bauen koennen."""
    return _build_ohlcv


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Frische, fluechtige Datenbank je Test.

    SQLite statt PostgreSQL, damit Integrationstests ohne laufenden Server
    moeglich sind. Dialektabhaengiges SQL ist deshalb in
    ``app.database.dialects`` gebuendelt.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session

    await engine.dispose()
