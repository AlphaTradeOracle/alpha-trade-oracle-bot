"""Aufbau der Telegram-Application."""

from __future__ import annotations

from telegram.ext import Application, ApplicationBuilder

from app.bot.handlers import BotHandlers
from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError
from app.core.logging import get_logger
from app.services.analysis_service import AnalysisService
from app.services.backtest_service import BacktestService
from app.services.paper_trading_service import PaperTradingService
from app.services.scan_service import ScanService

logger = get_logger(__name__)


def build_bot_application(
    analysis_service: AnalysisService,
    *,
    settings: Settings | None = None,
    scan_service: ScanService | None = None,
    backtest_service: BacktestService | None = None,
    paper_trading: PaperTradingService | None = None,
) -> Application:
    """Telegram-Application mit allen Handlern erzeugen."""
    cfg = settings or get_settings()
    token = cfg.telegram_bot_token.get_secret_value()

    if not token:
        raise ConfigurationError(
            "TELEGRAM_BOT_TOKEN ist nicht gesetzt.",
            detail="Token via @BotFather erzeugen und in die .env eintragen",
        )

    # get_updates_* timeouts are separate from send timeouts — long-poll
    # against api.telegram.org is where Bad Gateway spikes show up.
    application = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(True)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(15)
        .pool_timeout(15)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(40)
        .get_updates_pool_timeout(15)
        .build()
    )

    handlers = BotHandlers(
        cfg,
        analysis_service,
        scan_service=scan_service,
        backtest_service=backtest_service,
        paper_trading=paper_trading,
    )
    handlers.register(application)
    application.add_error_handler(_log_error)

    logger.info(
        "telegram_application_built",
        allowed_chats=len(cfg.allowed_chat_ids),
        admin_chats=len(cfg.admin_chat_ids),
    )
    return application


async def _log_error(update: object, context: object) -> None:
    """Fehler protokollieren, ohne den Bot zu stoppen."""
    error = getattr(context, "error", None)
    logger.error("telegram_handler_error", error=str(error), update=str(update)[:400])
