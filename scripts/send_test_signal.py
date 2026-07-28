#!/usr/bin/env python3
"""Send one Telegram test signal in the new compact look (chart + caption)."""

from __future__ import annotations

import asyncio
import sys

from telegram import Bot

from app.bot.formatting import format_signal_message
from app.bot.notifier import TelegramNotifier
from app.container import build_container
from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.signals.types import RiskParameters


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT")


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    if not settings.telegram_configured:
        print("Telegram not configured", file=sys.stderr)
        return 1

    chat_ids = sorted(settings.allowed_chat_ids)
    if not chat_ids:
        print("No TELEGRAM_ALLOWED_CHAT_IDS", file=sys.stderr)
        return 1
    chat_id = chat_ids[0]

    container = build_container(settings)
    notifier = TelegramNotifier(Bot(settings.telegram_bot_token.get_secret_value()), settings)

    outcome = None
    for symbol in SYMBOLS:
        print(f"Trying {symbol} ...")
        candidate = await container.analysis_service.analyze(
            symbol, persist=False, use_llm=False
        )
        direction = candidate.result.direction
        print(f"  -> {direction.value} score={candidate.result.score:.1f}")
        if direction.is_actionable and candidate.result.risk is not None:
            outcome = candidate
            break

    if outcome is None:
        # Fallback: real BTC chart + explicit demo levels so the new look is visible.
        print("No actionable setup — building demo LONG on BTCUSDT candles")
        outcome = await container.analysis_service.analyze(
            "BTCUSDT", persist=False, use_llm=False
        )
        price = float(outcome.result.reference_price or 100_000.0)
        risk_dist = price * 0.012
        outcome.result.direction = SignalDirection.STRONG_LONG
        outcome.result.score = 84.0
        outcome.result.reasons = [
            "Trendlage: 4h bullisch, 1h bullisch, 15m bullisch",
            (
                "EMA9 ueber EMA20; EMA20 ueber EMA50; Kurs ueber EMA200; "
                "SMA50 ueber SMA200 (Golden-Cross-Lage); Supertrend bullisch; "
                "ADX 27.3 bestaetigt einen Trend"
            ),
            (
                "RSI 60.8 bullisch ohne Ueberhitzung; RSI steigend; "
                "MACD-Histogramm positiv; ROC +5.4% positiv"
            ),
            "Volumenspitze (3.3x Durchschnitt); OBV steigend (Akkumulation)",
            "ATR 1.90% im gut handelbaren Bereich",
            "Struktur: hoehere Hochs und hoehere Tiefs",
        ]
        outcome.result.counter_arguments = [
            "Widerstand nahe Entry moeglich",
            "Gesamtmarkt kann das Setup invalidieren",
        ]
        outcome.result.risk = RiskParameters(
            entry_low=price * 0.998,
            entry_high=price * 1.002,
            stop_loss=price - risk_dist,
            take_profit_1=price + risk_dist * 2.0,
            take_profit_2=price + risk_dist * 4.0,
            take_profit_3=price + risk_dist * 6.0,
            risk_reward_ratio=2.0,
            risk_percent=1.0,
            suggested_position_size=0.01,
            stop_distance_percent=1.2,
            invalidation_note="1h-Schlusskurs unter Stop-Loss",
        )

    text = format_signal_message(
        outcome.result,
        price_precision=outcome.price_precision,
        display_timezone=settings.display_timezone,
        llm_analysis=None,
    )
    print(f"Sending to chat {chat_id} ...")
    print(text[:400].replace("\\", ""))
    ids = await notifier.send_analysis(chat_id, outcome, text)
    await container.aclose()
    if not ids:
        print("Send failed", file=sys.stderr)
        return 1
    print(f"OK message_ids={ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
