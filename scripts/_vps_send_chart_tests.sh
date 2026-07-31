#!/usr/bin/env bash
# Test: Signal-Chart + Performance-Digest mit Logo-Wasserzeichen.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

SYMBOL=$(docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -tAc \
  "SELECT a.symbol FROM signals s JOIN assets a ON a.id = s.asset_id
   WHERE s.is_dispatched = true ORDER BY s.created_at DESC LIMIT 1;" | tr -d '[:space:]')
if [[ -z "$SYMBOL" ]]; then
  SYMBOL="ATOMUSDT"
fi
echo "Using symbol: $SYMBOL"

cat >/tmp/_send_chart_tests.py <<'PY'
from __future__ import annotations

import asyncio
import os
import sys

from telegram import Bot

from app.bot.formatting import format_signal_message
from app.bot.notifier import TelegramNotifier
from app.container import build_container
from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.signals.types import RiskParameters


async def main() -> int:
    symbol = (os.environ.get("TEST_SYMBOL") or "BTCUSDT").upper()
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    if not settings.telegram_configured or not settings.allowed_chat_ids:
        print("Telegram not configured", file=sys.stderr)
        return 1

    container = build_container(settings)
    notifier = TelegramNotifier(
        Bot(settings.telegram_bot_token.get_secret_value()), settings
    )

    print(f"Analyzing {symbol} ...")
    outcome = await container.analysis_service.analyze(
        symbol, persist=False, use_llm=False
    )
    result = outcome.result
    print(f"  live -> {result.direction.value} score={result.score:.1f}")

    if not result.direction.is_actionable or result.risk is None:
        price = float(result.reference_price or 1.0)
        risk_dist = max(price * 0.012, 1e-8)
        side = SignalDirection.STRONG_SHORT
        if "LONG" in result.direction.value.upper():
            side = SignalDirection.STRONG_LONG
        result.direction = side
        result.score = max(result.score, 78.0)
        if side.is_long:
            result.risk = RiskParameters(
                entry_low=price * 0.998,
                entry_high=price * 1.002,
                stop_loss=price - risk_dist,
                take_profit_1=price + risk_dist * 1.5,
                take_profit_2=price + risk_dist * 2.5,
                take_profit_3=price + risk_dist * 4.0,
                risk_reward_ratio=2.0,
                risk_percent=1.0,
                suggested_position_size=0.01,
                stop_distance_percent=1.2,
                invalidation_note="Demo-Levels fuer Chart-Test",
            )
        else:
            result.risk = RiskParameters(
                entry_low=price * 0.998,
                entry_high=price * 1.002,
                stop_loss=price + risk_dist,
                take_profit_1=price - risk_dist * 1.5,
                take_profit_2=price - risk_dist * 2.5,
                take_profit_3=price - risk_dist * 4.0,
                risk_reward_ratio=2.0,
                risk_percent=1.0,
                suggested_position_size=0.01,
                stop_distance_percent=1.2,
                invalidation_note="Demo-Levels fuer Chart-Test",
            )
        print(f"  forced demo {side.value}")

    text = format_signal_message(
        result,
        price_precision=outcome.price_precision,
        display_timezone=settings.display_timezone,
        llm_analysis=None,
    )
    sent = 0
    for chat_id in sorted(settings.allowed_chat_ids):
        ids = await notifier.send_analysis(chat_id, outcome, text)
        print(f"  signal chat {chat_id}: {ids}")
        if ids:
            sent += 1
    await container.aclose()
    print(f"Signal sent to {sent} chat(s)")
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
PY

docker compose cp /tmp/_send_chart_tests.py worker:/tmp/_send_chart_tests.py
docker compose exec -T -e TEST_SYMBOL="$SYMBOL" worker python /tmp/_send_chart_tests.py

echo "==> Performance digest"
docker compose exec -T worker python -m app.cli paper digest --send
echo "DONE"
