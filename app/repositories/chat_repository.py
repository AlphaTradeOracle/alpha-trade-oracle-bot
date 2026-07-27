"""Datenzugriff fuer Telegram-Chats und Watchlists."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.market import Asset
from app.models.user import TelegramChat, Watchlist


class ChatRepository:
    """Telegram-Chats."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_chat_id(self, chat_id: int) -> TelegramChat | None:
        result = await self._session.execute(
            select(TelegramChat).where(TelegramChat.chat_id == chat_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        chat_id: int,
        *,
        chat_type: str = "private",
        title: str | None = None,
        is_admin: bool = False,
    ) -> TelegramChat:
        existing = await self.get_by_chat_id(chat_id)
        if existing is not None:
            # Admin-Status folgt immer der Konfiguration, nicht dem gespeicherten Wert.
            if existing.is_admin != is_admin:
                existing.is_admin = is_admin
            if title and existing.title != title:
                existing.title = title
            return existing

        chat = TelegramChat(chat_id=chat_id, chat_type=chat_type, title=title, is_admin=is_admin)
        self._session.add(chat)
        await self._session.flush()
        return chat

    async def list_active_with_notifications(self) -> list[TelegramChat]:
        result = await self._session.execute(
            select(TelegramChat).where(
                TelegramChat.is_active.is_(True),
                TelegramChat.notifications_enabled.is_(True),
            )
        )
        return list(result.scalars())

    async def set_notifications(self, chat_id: int, enabled: bool) -> TelegramChat | None:
        chat = await self.get_by_chat_id(chat_id)
        if chat is not None:
            chat.notifications_enabled = enabled
        return chat

    async def set_min_score_override(
        self, chat_id: int, min_score: float | None
    ) -> TelegramChat | None:
        chat = await self.get_by_chat_id(chat_id)
        if chat is not None:
            chat.min_score_override = min_score
        return chat


class WatchlistRepository:
    """Watchlist-Eintraege je Chat."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, telegram_chat_id: int, asset_id: int, *, timeframes: str | None = None
    ) -> tuple[Watchlist, bool]:
        """Eintrag anlegen oder reaktivieren. Rueckgabe: ``(entry, created)``."""
        result = await self._session.execute(
            select(Watchlist).where(
                Watchlist.telegram_chat_id == telegram_chat_id,
                Watchlist.asset_id == asset_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            was_inactive = not existing.is_active
            existing.is_active = True
            if timeframes:
                existing.timeframes = timeframes
            return existing, was_inactive

        entry = Watchlist(
            telegram_chat_id=telegram_chat_id, asset_id=asset_id, timeframes=timeframes
        )
        self._session.add(entry)
        await self._session.flush()
        return entry, True

    async def remove(self, telegram_chat_id: int, asset_id: int) -> bool:
        """Eintrag deaktivieren. Historie bleibt fuer Auswertungen erhalten."""
        result = await self._session.execute(
            select(Watchlist).where(
                Watchlist.telegram_chat_id == telegram_chat_id,
                Watchlist.asset_id == asset_id,
                Watchlist.is_active.is_(True),
            )
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            return False
        entry.is_active = False
        return True

    async def list_for_chat(self, telegram_chat_id: int) -> list[tuple[Watchlist, Asset]]:
        result = await self._session.execute(
            select(Watchlist, Asset)
            .join(Asset, Watchlist.asset_id == Asset.id)
            .where(
                Watchlist.telegram_chat_id == telegram_chat_id,
                Watchlist.is_active.is_(True),
            )
            .order_by(Asset.symbol)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def list_all_active(self) -> list[tuple[Watchlist, Asset, int]]:
        """Alle aktiven Eintraege mit Symbol und Telegram-Chat-ID fuer Scans."""
        result = await self._session.execute(
            select(Watchlist, Asset, TelegramChat.chat_id)
            .join(Asset, Watchlist.asset_id == Asset.id)
            .join(TelegramChat, Watchlist.telegram_chat_id == TelegramChat.id)
            .where(
                Watchlist.is_active.is_(True),
                TelegramChat.is_active.is_(True),
                TelegramChat.notifications_enabled.is_(True),
            )
            .options(selectinload(Watchlist.chat))
        )
        return [(row[0], row[1], row[2]) for row in result.all()]

    async def distinct_watched_symbols(self) -> list[str]:
        """Symbole, die mindestens ein aktiver Chat beobachtet."""
        result = await self._session.execute(
            select(Asset.symbol)
            .join(Watchlist, Watchlist.asset_id == Asset.id)
            .join(TelegramChat, Watchlist.telegram_chat_id == TelegramChat.id)
            .where(
                Watchlist.is_active.is_(True),
                TelegramChat.is_active.is_(True),
                TelegramChat.notifications_enabled.is_(True),
            )
            .distinct()
        )
        return sorted(result.scalars())
