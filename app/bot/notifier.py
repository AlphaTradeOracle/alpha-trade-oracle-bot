"""Telegram-Versand mit Rate-Limit-Beachtung und Nachrichtenaufteilung."""

from __future__ import annotations

import asyncio
from io import BytesIO

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError

from app.bot.formatting import format_signal_message, split_message
from app.charts.signal_chart import build_signal_chart
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.repositories.chat_repository import ChatRepository
from app.services.analysis_service import AnalysisOutcome
from app.services.scan_service import SignalDispatcher

logger = get_logger(__name__)

#: Telegram erlaubt rund 30 Nachrichten pro Sekunde global. Der Abstand haelt
#: den Bot mit Sicherheitsabstand darunter.
SEND_INTERVAL_SECONDS = 0.05

#: Maximale Wartezeit, die ein RetryAfter-Hinweis auslösen darf.
MAX_RETRY_AFTER_SECONDS = 30.0


class TelegramNotifier:
    """Versendet Nachrichten und respektiert die Telegram-Rate-Limits."""

    def __init__(self, bot: Bot, settings: Settings | None = None) -> None:
        self._bot = bot
        self._settings = settings or get_settings()
        self._lock = asyncio.Lock()

    async def send(self, chat_id: int, text: str) -> list[int]:
        """Nachricht senden, bei Bedarf aufgeteilt. Rueckgabe: Message-IDs."""
        message_ids: list[int] = []
        for part in split_message(text):
            message_id = await self._send_part(chat_id, part)
            if message_id is not None:
                message_ids.append(message_id)
        return message_ids

    async def send_photo(self, chat_id: int, photo: bytes) -> int | None:
        """Signal-Chart als Bild senden."""
        async with self._lock:
            try:
                message = await self._bot.send_photo(chat_id=chat_id, photo=BytesIO(photo))
                await asyncio.sleep(SEND_INTERVAL_SECONDS)
                return message.message_id
            except TelegramError as exc:
                logger.warning("telegram_photo_failed", chat_id=chat_id, error=str(exc))
                return None

    async def send_analysis(
        self, chat_id: int, outcome: AnalysisOutcome, text: str
    ) -> list[int]:
        """Chart (oben) und Analyse-Text senden."""
        message_ids: list[int] = []
        chart = build_signal_chart(outcome)
        if chart is not None:
            photo_id = await self.send_photo(chat_id, chart)
            if photo_id is not None:
                message_ids.append(photo_id)
        message_ids.extend(await self.send(chat_id, text))
        return message_ids

    async def _send_part(self, chat_id: int, text: str) -> int | None:
        """Einen Nachrichtenteil senden.

        Bei ``RetryAfter`` wird genau einmal gewartet und erneut versucht.
        Schlaegt MarkdownV2 fehl (etwa wegen einer Auszeichnung, die Telegram
        nicht akzeptiert), wird als Fallback ohne Formatierung gesendet — eine
        unformatierte Nachricht ist besser als keine.
        """
        async with self._lock:
            for attempt in (1, 2):
                try:
                    message = await self._bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        disable_web_page_preview=True,
                    )
                    await asyncio.sleep(SEND_INTERVAL_SECONDS)
                    return message.message_id
                except RetryAfter as exc:
                    retry_after = exc.retry_after
                    wait_seconds = (
                        float(retry_after.total_seconds())
                        if hasattr(retry_after, "total_seconds")
                        else float(retry_after)
                    )
                    wait_for = min(wait_seconds + 0.5, MAX_RETRY_AFTER_SECONDS)
                    logger.warning("telegram_rate_limited", chat_id=chat_id, wait_seconds=wait_for)
                    if attempt == 2:
                        raise
                    await asyncio.sleep(wait_for)
                except TelegramError as exc:
                    if attempt == 1 and "can't parse entities" in str(exc).lower():
                        logger.warning(
                            "telegram_markdown_fallback", chat_id=chat_id, error=str(exc)
                        )
                        plain = _strip_markdown(text)
                        message = await self._bot.send_message(
                            chat_id=chat_id, text=plain, disable_web_page_preview=True
                        )
                        await asyncio.sleep(SEND_INTERVAL_SECONDS)
                        return message.message_id
                    raise
        return None

    async def health_check(self) -> bool:
        try:
            await self._bot.get_me()
            return True
        except Exception as exc:
            logger.warning("telegram_health_check_failed", error=str(exc))
            return False


class TelegramSignalDispatcher(SignalDispatcher):
    """Stellt Signale an alle aktiven Chats zu.

    Implementiert das Dispatcher-Interface des ScanService, sodass dieser
    Telegram nicht direkt kennen muss.
    """

    def __init__(
        self,
        notifier: TelegramNotifier,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self._notifier = notifier
        self._session = session
        self._settings = settings or get_settings()

    async def dispatch(self, outcome: AnalysisOutcome) -> list[tuple[int, int | None, str | None]]:
        chats = await ChatRepository(self._session).list_active_with_notifications()
        if not chats:
            logger.info("no_active_chats_for_dispatch", symbol=outcome.result.symbol)
            return []

        text = format_signal_message(
            outcome.result,
            price_precision=outcome.price_precision,
            display_timezone=self._settings.display_timezone,
            llm_analysis=outcome.llm_analysis,
        )

        results: list[tuple[int, int | None, str | None]] = []
        for chat in chats:
            # Ein chat-spezifischer Mindestscore darf strenger sein als der globale.
            threshold = (
                float(chat.min_score_override)
                if chat.min_score_override is not None
                else self._settings.signal_min_score
            )
            if outcome.result.score < threshold:
                logger.debug(
                    "chat_threshold_not_met",
                    chat_id=chat.chat_id,
                    score=outcome.result.score,
                    threshold=threshold,
                )
                continue

            try:
                message_ids = await self._notifier.send_analysis(chat.chat_id, outcome, text)
                results.append((chat.id, message_ids[0] if message_ids else None, None))
                logger.info(
                    "signal_delivered",
                    chat_id=chat.chat_id,
                    symbol=outcome.result.symbol,
                    direction=outcome.result.direction.value,
                )
            except Exception as exc:
                results.append((chat.id, None, str(exc)))
                logger.warning(
                    "signal_delivery_failed",
                    chat_id=chat.chat_id,
                    symbol=outcome.result.symbol,
                    error=str(exc),
                )

        return results


def _strip_markdown(text: str) -> str:
    """MarkdownV2-Auszeichnungen entfernen fuer den unformatierten Fallback."""
    without_escapes = text.replace("\\", "")
    return without_escapes.replace("*", "").replace("_", "")
