"""Telegram-Versand mit Rate-Limit-Beachtung und Nachrichtenaufteilung."""

from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError

from app.bot.formatting import (
    format_paper_digest_message,
    format_signal_message,
    infer_price_precision,
    signal_result_from_paper_position,
    split_caption_and_body,
    split_message,
)
from app.charts.signal_chart import build_paper_trade_chart, resolve_paper_chart_timeframe
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.market_data.base import MarketDataProvider
from app.models.paper import PaperPosition
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

    async def notify_allowed_chats(self, text: str) -> int:
        """Text an alle TELEGRAM_ALLOWED_CHAT_IDS senden. Rueckgabe: Chat-Anzahl."""
        sent = 0
        for chat_id in sorted(self._settings.allowed_chat_ids):
            try:
                await self.send(chat_id, text)
                sent += 1
            except Exception as exc:
                logger.warning(
                    "telegram_allowed_notify_failed",
                    chat_id=chat_id,
                    error=str(exc),
                )
        return sent

    async def notify_paper_digest(self, snapshot: object) -> int:
        """Stuendlichen Paper-Digest formatieren und versenden (Equity-Chart oben)."""
        from app.charts.paper_equity_chart import build_paper_equity_chart
        from app.services.paper_trading_service import PaperDigestSnapshot

        if not isinstance(snapshot, PaperDigestSnapshot):
            raise TypeError("snapshot must be PaperDigestSnapshot")
        text = format_paper_digest_message(
            snapshot,
            display_timezone=self._settings.display_timezone,
        )
        chart: bytes | None = None
        curve = snapshot.equity_curve or []
        if len(curve) >= 2:
            chart = build_paper_equity_chart(
                curve,
                initial=float(snapshot.summary.initial_balance),
                title="EQUITY",
                subtitle="Cash + Open PnL  ·  Performance Dashboard",
            )
        if chart is None:
            return await self.notify_allowed_chats(text)

        sent = 0
        for chat_id in sorted(self._settings.allowed_chat_ids):
            try:
                ids = await self.send_with_chart(chat_id, text, chart)
                if ids:
                    sent += 1
            except Exception as exc:
                logger.warning(
                    "telegram_paper_digest_failed",
                    chat_id=chat_id,
                    error=str(exc),
                )
        return sent

    async def send_photo(
        self, chat_id: int, photo: bytes, caption: str | None = None
    ) -> int | None:
        """Signal-Chart als Bild senden, optional mit MarkdownV2-Caption."""
        async with self._lock:
            try:
                kwargs: dict = {"chat_id": chat_id, "photo": BytesIO(photo)}
                if caption:
                    kwargs["caption"] = caption
                    kwargs["parse_mode"] = ParseMode.MARKDOWN_V2
                message = await self._bot.send_photo(**kwargs)
                await asyncio.sleep(SEND_INTERVAL_SECONDS)
                return message.message_id
            except TelegramError as exc:
                if caption and "can't parse entities" in str(exc).lower():
                    logger.warning(
                        "telegram_photo_caption_fallback", chat_id=chat_id, error=str(exc)
                    )
                    try:
                        plain = _strip_markdown(caption)
                        message = await self._bot.send_photo(
                            chat_id=chat_id, photo=BytesIO(photo), caption=plain
                        )
                        await asyncio.sleep(SEND_INTERVAL_SECONDS)
                        return message.message_id
                    except TelegramError as nested:
                        logger.warning(
                            "telegram_photo_failed", chat_id=chat_id, error=str(nested)
                        )
                        return None
                logger.warning("telegram_photo_failed", chat_id=chat_id, error=str(exc))
                return None

    async def send_with_chart(
        self, chat_id: int, text: str, chart: bytes | None
    ) -> list[int]:
        """Signal-Text mit optionalem Chart (Caption + Rest als Text)."""
        if chart is None:
            return await self.send(chat_id, text)

        message_ids: list[int] = []
        caption, body = split_caption_and_body(text)
        photo_id = await self.send_photo(chat_id, chart, caption=caption)
        if photo_id is not None:
            message_ids.append(photo_id)
            if body is not None:
                message_ids.extend(await self.send(chat_id, body))
            return message_ids

        return await self.send(chat_id, text)

    async def send_analysis(
        self, chat_id: int, outcome: AnalysisOutcome, text: str
    ) -> list[int]:
        """Chart oben mit Caption wenn moeglich, sonst Chart + Text darunter."""
        from app.charts.signal_chart import build_signal_chart

        chart = build_signal_chart(outcome)
        return await self.send_with_chart(chat_id, text, chart)

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


class PaperTradeNotifier(Protocol):
    """Benachrichtigung bei Paper-Trade-Ereignissen."""

    async def notify_open(
        self,
        position: PaperPosition,
        *,
        retest_fill: bool = False,
        reasons: list[str] | None = None,
    ) -> None: ...

    async def notify_close(self, position: PaperPosition) -> None: ...


class TelegramPaperTradeNotifier:
    """Paper-Trade-Open wie Signal (Chart + Levels) an TELEGRAM_ALLOWED_CHAT_IDS."""

    def __init__(
        self,
        notifier: TelegramNotifier,
        market_data: MarketDataProvider,
        settings: Settings | None = None,
    ) -> None:
        self._notifier = notifier
        self._market_data = market_data
        self._settings = settings or get_settings()

    async def notify_open(
        self,
        position: PaperPosition,
        *,
        retest_fill: bool = False,
        reasons: list[str] | None = None,
    ) -> None:
        chat_ids = sorted(self._settings.allowed_chat_ids)
        if not chat_ids:
            logger.debug("paper_trade_notify_skipped_no_chats", symbol=position.symbol)
            return

        result = signal_result_from_paper_position(position, reasons=reasons)
        price_precision = infer_price_precision(float(position.entry_price))
        text = format_signal_message(
            result,
            price_precision=price_precision,
            display_timezone=self._settings.display_timezone,
        )

        chart_tf = resolve_paper_chart_timeframe(
            position.timeframe or self._settings.primary_timeframe,
            self._settings,
        )
        chart: bytes | None = None
        try:
            series = await self._market_data.get_candles(
                position.symbol,
                chart_tf,
                limit=self._settings.candle_limit,
            )
            if series is not None:
                chart = build_paper_trade_chart(
                    position,
                    series,
                    price_precision=price_precision,
                )
        except Exception as exc:
            logger.warning(
                "paper_trade_chart_fetch_failed",
                symbol=position.symbol,
                timeframe=chart_tf,
                error=str(exc),
            )

        for chat_id in chat_ids:
            try:
                await self._notifier.send_with_chart(chat_id, text, chart)
                logger.info(
                    "paper_trade_open_notified",
                    chat_id=chat_id,
                    symbol=position.symbol,
                    retest_fill=retest_fill,
                    chart_timeframe=chart_tf,
                    chart_sent=chart is not None,
                )
            except Exception as exc:
                logger.warning(
                    "paper_trade_open_notify_failed",
                    chat_id=chat_id,
                    symbol=position.symbol,
                    error=str(exc),
                )

    async def notify_close(self, position: PaperPosition) -> None:
        """Close-Meldungen sind deaktiviert — nur Open wird an Telegram gesendet."""
        return


def _strip_markdown(text: str) -> str:
    """MarkdownV2-Auszeichnungen entfernen fuer den unformatierten Fallback."""
    without_escapes = text.replace("\\", "")
    return without_escapes.replace("*", "").replace("_", "")
