"""Grunddaten anlegen.

Legt die Standardsymbole, die Standardstrategie und deren erste Gewichtungs-
version an. Das Skript ist idempotent: ein erneuter Aufruf erzeugt keine
Duplikate und ueberschreibt keine bestehende aktive Strategieversion.

Es werden keine Secrets geschrieben. Telegram-Chats entstehen erst, wenn sich
ein erlaubter Chat per ``/start`` beim Bot meldet.
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.database.session import session_scope
from app.market_data.factory import create_market_data_provider
from app.market_data.types import SymbolInfo
from app.repositories.asset_repository import AssetRepository
from app.repositories.strategy_repository import StrategyRepository
from app.strategies.weights import StrategyWeights

logger = get_logger(__name__)

DEFAULT_STRATEGY_NAME = "default"
DEFAULT_STRATEGY_DESCRIPTION = (
    "Regelbasierte Multi-Timeframe-Strategie mit den Standardgewichten aus "
    "docs/SIGNAL_LOGIC.md. Aenderungen an den Gewichten erzeugen immer eine "
    "neue Version, niemals eine Ueberschreibung."
)


async def run_seed() -> None:
    """Grunddaten anlegen und das Ergebnis protokollieren."""
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)

    provider = create_market_data_provider(settings)

    try:
        async with session_scope() as session:
            assets = AssetRepository(session)
            created_symbols: list[str] = []

            for symbol in settings.symbols:
                # Praezisionen vom Provider holen, damit Preise korrekt gerundet
                # werden. Ist der Provider nicht erreichbar, greifen Defaults.
                try:
                    info = await provider.get_symbol_info(symbol)
                except Exception as exc:
                    logger.warning(
                        "seed_symbol_info_unavailable", symbol=symbol, error=str(exc)
                    )
                    quote = settings.default_quote_asset
                    base = symbol.removesuffix(quote) or symbol
                    info = SymbolInfo(
                        symbol=symbol,
                        base_asset=base,
                        quote_asset=quote,
                    )

                asset = await assets.get_or_create(info, exchange=provider.name)
                created_symbols.append(asset.symbol)

            strategies = StrategyRepository(session)
            await strategies.get_or_create_strategy(
                DEFAULT_STRATEGY_NAME, description=DEFAULT_STRATEGY_DESCRIPTION
            )

            active = await strategies.get_active_version(DEFAULT_STRATEGY_NAME)
            if active is None:
                version = await strategies.create_version(
                    StrategyWeights(),
                    name=DEFAULT_STRATEGY_NAME,
                    min_score=settings.signal_min_score,
                    min_risk_reward_ratio=settings.min_risk_reward_ratio,
                    atr_multiplier=settings.atr_multiplier,
                    notes="Initiale Version aus dem Seed-Skript.",
                    activate=True,
                )
                logger.info(
                    "seed_strategy_version_created",
                    strategy=DEFAULT_STRATEGY_NAME,
                    version=version.version,
                )
            else:
                logger.info(
                    "seed_strategy_version_exists",
                    strategy=DEFAULT_STRATEGY_NAME,
                    version=active.version,
                )

        logger.info("seed_completed", symbols=created_symbols)
        print("Grunddaten angelegt:")
        for symbol in created_symbols:
            print(f"  Asset:    {symbol}")
        print(f"  Strategie: {DEFAULT_STRATEGY_NAME}")
        print(
            "\nTelegram-Chats werden nicht per Seed erzeugt. Setze "
            "TELEGRAM_ALLOWED_CHAT_IDS in der .env und sende dem Bot /start."
        )
    finally:
        await provider.close()


if __name__ == "__main__":
    asyncio.run(run_seed())
