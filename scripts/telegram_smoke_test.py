"""Einmaliger Telegram-Smoke-Test ohne PostgreSQL/Redis.

Ablauf:
1. BTCUSDT (oder ``--symbol``) ueber Binance analysieren
2. Formatierte MarkdownV2-Nachricht an die erste erlaubte Chat-ID senden

Voraussetzung: ``.env`` mit ``TELEGRAM_BOT_TOKEN`` und ``TELEGRAM_ALLOWED_CHAT_IDS``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from telegram import Bot

from app.bot.formatting import format_analysis_message, format_score_breakdown, split_message
from app.bot.notifier import TelegramNotifier
from app.container import build_container
from app.core.config import get_settings
from app.core.errors import AlphaTradeOracleError
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


async def run(symbol: str, *, use_llm: bool, chat_id: int | None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)

    if not settings.telegram_configured:
        print(
            "TELEGRAM_BOT_TOKEN fehlt. Token bei @BotFather holen und in .env eintragen.",
            file=sys.stderr,
        )
        return 1

    chat_ids = sorted(settings.allowed_chat_ids)
    target_chat = chat_id if chat_id is not None else (chat_ids[0] if chat_ids else None)
    if target_chat is None:
        print(
            "Keine Chat-ID. TELEGRAM_ALLOWED_CHAT_IDS setzen oder --chat-id uebergeben.",
            file=sys.stderr,
        )
        return 1

    container = build_container(settings)

    try:
        print(f"Analysiere {symbol.upper()} ...")
        outcome = await container.analysis_service.analyze(
            symbol,
            persist=False,
            use_llm=use_llm if settings.enable_llm_analysis else False,
        )
        result = outcome.result
        print(
            f"Signal: {result.direction.value} | Score: {result.score:.1f} | "
            f"Konfidenz: {result.confidence.value}"
        )

        notifier = TelegramNotifier(
            Bot(settings.telegram_bot_token.get_secret_value()), settings
        )
        message = format_analysis_message(
            result,
            price_precision=outcome.price_precision,
            display_timezone=settings.display_timezone,
            llm_analysis=outcome.llm_analysis,
        )
        breakdown = format_score_breakdown(result)

        print(f"Sende Analyse an Chat {target_chat} ...")
        for part in split_message(message):
            message_id = await notifier.send(target_chat, part)
            if message_id is None:
                print("Telegram-Versand fehlgeschlagen.", file=sys.stderr)
                return 1

        await notifier.send(target_chat, breakdown)
        print("Fertig. Pruefe Telegram — die Analyse sollte dort angekommen sein.")
        return 0
    except AlphaTradeOracleError as exc:
        print(f"Analyse nicht moeglich: {exc}", file=sys.stderr)
        return 1
    finally:
        await container.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram-Smoke-Test fuer Alpha Trade Oracle")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument(
        "--llm",
        action="store_true",
        help="LLM-Zusammenfassung anfordern (braucht LLM_API_KEY)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.symbol, use_llm=args.llm, chat_id=args.chat_id)))


if __name__ == "__main__":
    main()
