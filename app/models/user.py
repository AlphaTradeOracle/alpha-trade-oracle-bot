"""Modelle fuer Benutzer, Telegram-Chats und Watchlists."""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import SCORE, Base, TimestampMixin


class User(Base, TimestampMixin):
    """Fachlicher Benutzer. Im MVP meist 1:1 zu einem Telegram-Chat."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_ref: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    chats: Mapped[list[TelegramChat]] = relationship(back_populates="user")


class TelegramChat(Base, TimestampMixin):
    """Ein autorisierter Telegram-Chat (privat oder Gruppe)."""

    __tablename__ = "telegram_chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    chat_type: Mapped[str] = mapped_column(String(32), nullable=False, default="private")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Ueberschreibt den globalen Mindestscore fuer diesen Chat, falls gesetzt.
    min_score_override: Mapped[float | None] = mapped_column(SCORE, nullable=True)

    user: Mapped[User | None] = relationship(back_populates="chats")
    watchlist_entries: Mapped[list[Watchlist]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )


class Watchlist(Base, TimestampMixin):
    """Ein von einem Chat beobachtetes Instrument."""

    __tablename__ = "watchlists"
    __table_args__ = (
        UniqueConstraint("telegram_chat_id", "asset_id", name="uq_watchlist_chat_asset"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_chats.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    #: Leer bedeutet: Standard-Timeframes aus der Konfiguration verwenden.
    timeframes: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    chat: Mapped[TelegramChat] = relationship(back_populates="watchlist_entries")
