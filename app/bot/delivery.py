"""Gemeinsame Zustellung von Analyse-Nachrichten inkl. Chart."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from io import BytesIO

from telegram import Bot, Message
from telegram.constants import ParseMode

from app.bot.formatting import split_caption_and_body
from app.charts.signal_chart import build_signal_chart
from app.core.logging import get_logger
from app.services.analysis_service import AnalysisOutcome

logger = get_logger(__name__)

SendText = Callable[[str], Awaitable[None]]
SendPhoto = Callable[[bytes, str | None], Awaitable[None]]


async def deliver_analysis_with_chart(
    outcome: AnalysisOutcome,
    text: str,
    *,
    send_text: SendText,
    send_photo: SendPhoto | None = None,
) -> bool:
    """Chart oben (mit Caption wenn moeglich), sonst Text darunter."""
    chart = build_signal_chart(outcome)
    if chart is None or send_photo is None:
        await send_text(text)
        return False

    caption, body = split_caption_and_body(text)
    try:
        await send_photo(chart, caption)
    except Exception as exc:
        logger.warning(
            "signal_chart_delivery_failed",
            symbol=outcome.result.symbol,
            error=str(exc),
        )
        await send_text(text)
        return False

    if body is not None:
        await send_text(body)
    return True


async def reply_photo(message: Message, photo: bytes, caption: str | None = None) -> None:
    """Foto als Antwort auf eine Telegram-Nachricht senden."""
    kwargs: dict = {"photo": BytesIO(photo)}
    if caption:
        kwargs["caption"] = caption
        kwargs["parse_mode"] = ParseMode.MARKDOWN_V2
    await message.reply_photo(**kwargs)


def photo_sender(bot: Bot, chat_id: int) -> SendPhoto:
    """Photo-Sender fuer :class:`~app.bot.notifier.TelegramNotifier`."""

    async def _send(photo: bytes, caption: str | None = None) -> None:
        kwargs: dict = {"chat_id": chat_id, "photo": BytesIO(photo)}
        if caption:
            kwargs["caption"] = caption
            kwargs["parse_mode"] = ParseMode.MARKDOWN_V2
        await bot.send_photo(**kwargs)

    return _send
