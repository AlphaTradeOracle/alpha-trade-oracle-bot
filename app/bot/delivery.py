"""Gemeinsame Zustellung von Analyse-Nachrichten inkl. Chart."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from io import BytesIO

from telegram import Bot, Message

from app.charts.signal_chart import build_signal_chart
from app.core.logging import get_logger
from app.services.analysis_service import AnalysisOutcome

logger = get_logger(__name__)

SendText = Callable[[str], Awaitable[None]]
SendPhoto = Callable[[bytes], Awaitable[None]]


async def deliver_analysis_with_chart(
    outcome: AnalysisOutcome,
    text: str,
    *,
    send_text: SendText,
    send_photo: SendPhoto | None = None,
) -> bool:
    """Chart (falls moeglich) und Analyse-Text senden. Gibt True zurueck wenn Chart gesendet."""
    chart = build_signal_chart(outcome)
    if chart is None:
        await send_text(text)
        return False

    if send_photo is not None:
        try:
            await send_photo(chart)
        except Exception as exc:
            logger.warning(
                "signal_chart_delivery_failed",
                symbol=outcome.result.symbol,
                error=str(exc),
            )
    await send_text(text)
    return chart is not None


async def reply_photo(message: Message, photo: bytes) -> None:
    """Foto als Antwort auf eine Telegram-Nachricht senden."""
    await message.reply_photo(photo=BytesIO(photo))


def photo_sender(bot: Bot, chat_id: int) -> SendPhoto:
    """Photo-Sender fuer :class:`~app.bot.notifier.TelegramNotifier`."""

    async def _send(photo: bytes) -> None:
        await bot.send_photo(chat_id=chat_id, photo=BytesIO(photo))

    return _send
