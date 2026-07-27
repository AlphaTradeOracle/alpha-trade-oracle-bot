"""Zugriffskontrolle des Telegram-Bots.

Zwei Stufen: eine Allowlist erlaubter Chats und eine getrennte Admin-Allowlist.
Ist keine Allowlist konfiguriert, wird **kein** Zugriff gewaehrt — ein offener
Bot waere ein Sicherheitsrisiko und wuerde fremde Chats mit Signalen versorgen.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class AccessControl:
    """Prueft, ob ein Chat den Bot beziehungsweise Admin-Kommandos nutzen darf."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def has_allowlist(self) -> bool:
        return bool(self._settings.allowed_chat_ids or self._settings.admin_chat_ids)

    def is_allowed(self, chat_id: int) -> bool:
        """Admins sind implizit immer erlaubt, auch ohne Eintrag in der Allowlist."""
        if self.is_admin(chat_id):
            return True
        return chat_id in self._settings.allowed_chat_ids

    def is_admin(self, chat_id: int) -> bool:
        return chat_id in self._settings.admin_chat_ids

    def denial_message(self, chat_id: int) -> str:
        if not self.has_allowlist:
            return (
                "Dieser Bot ist noch nicht freigeschaltet. Es ist keine Chat-ID "
                "konfiguriert.\n\n"
                f"Deine Chat-ID lautet: {chat_id}\n\n"
                "Trage sie in TELEGRAM_ALLOWED_CHAT_IDS ein und starte den Bot neu."
            )
        return (
            "Zugriff verweigert. Dieser Chat ist nicht freigegeben.\n\n"
            f"Deine Chat-ID lautet: {chat_id}"
        )

    @staticmethod
    def admin_denial_message() -> str:
        return "Dieses Kommando ist auf Administratoren beschraenkt."
