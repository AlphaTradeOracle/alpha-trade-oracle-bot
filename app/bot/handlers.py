"""Telegram-Kommandos.

Jeder Handler ist schmal gehalten: autorisieren, Argumente pruefen, Service
aufrufen, Ergebnis formatieren. Die Fachlogik liegt in den Services.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from app.bot.auth import AccessControl
from app.bot.delivery import deliver_analysis_with_chart, reply_photo
from app.bot.formatting import (
    DISCLAIMER,
    escape_markdown_v2,
    format_analysis_message,
    format_score_breakdown,
    format_signal_message,
    split_message,
)
from app.core.config import Settings
from app.core.errors import AlphaTradeOracleError
from app.core.logging import get_logger, set_correlation_id
from app.core.time import utc_now
from app.database.session import session_scope
from app.repositories.asset_repository import AssetRepository
from app.repositories.chat_repository import ChatRepository, WatchlistRepository
from app.repositories.event_repository import ScheduledJobRepository
from app.repositories.paper_repository import PaperRepository
from app.repositories.signal_repository import SignalRepository
from app.services.analysis_service import AnalysisOutcome, AnalysisService
from app.services.backtest_service import BacktestService
from app.services.paper_trading_service import PaperTradingService
from app.services.scan_service import ScanService

logger = get_logger(__name__)

HELP_TEXT = """\
*Alpha Trade Oracle Bot*

Verfuegbare Kommandos:

/analyze SYMBOL — vollstaendige Marktanalyse, z. B. /analyze BTCUSDT
/signal SYMBOL — nur das Signal, kompakt
/watch SYMBOL — Symbol zur Watchlist hinzufuegen
/unwatch SYMBOL — Symbol aus der Watchlist entfernen
/watchlist — aktuelle Watchlist anzeigen
/performance — Auswertung der erzeugten Signale
/paper — Paper-Trading Depot und offene Positionen
/settings — aktuelle Konfiguration anzeigen
/status — Systemstatus
/help — diese Uebersicht

Der Bot fuehrt keine echten Trades aus und greift nicht auf Wallets zu.
Paper-Trading ist rein virtuell.\
"""

ADMIN_HELP_TEXT = """\
*Admin-Kommandos*

/admin\\_status — Detailstatus inkl. Hintergrundjobs
/run\\_scan — Marktscan sofort ausloesen
/backtest SYMBOL \\[TIMEFRAME\\] \\[TAGE\\] — Backtest ausfuehren
/reload\\_config — Konfiguration neu laden\
"""


class BotHandlers:
    """Buendelt alle Kommando-Handler und ihre Abhaengigkeiten."""

    def __init__(
        self,
        settings: Settings,
        analysis_service: AnalysisService,
        *,
        scan_service: ScanService | None = None,
        backtest_service: BacktestService | None = None,
        paper_trading: PaperTradingService | None = None,
    ) -> None:
        self._settings = settings
        self._analysis = analysis_service
        self._scan = scan_service
        self._backtest = backtest_service
        self._paper = paper_trading
        self._access = AccessControl(settings)

    def register(self, application: Application) -> None:
        """Alle Handler bei der Telegram-Application anmelden."""
        commands: dict[str, Callable[..., Awaitable[None]]] = {
            "start": self.start,
            "help": self.help,
            "status": self.status,
            "analyze": self.analyze,
            "signal": self.signal,
            "watch": self.watch,
            "unwatch": self.unwatch,
            "watchlist": self.watchlist,
            "performance": self.performance,
            "paper": self.paper,
            "settings": self.settings_command,
            "admin_status": self.admin_status,
            "run_scan": self.run_scan,
            "backtest": self.backtest,
            "reload_config": self.reload_config,
        }
        for name, handler in commands.items():
            application.add_handler(CommandHandler(name, handler))  # type: ignore[arg-type]

    # --- Basiskommandos ---------------------------------------------------

    async def start(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None:
            return

        if not await self._authorize(update, chat_id):
            return

        await self._register_chat(chat_id, update)
        text = (
            f"*{escape_markdown_v2('Willkommen beim Alpha Trade Oracle Bot')}*\n\n"
            + escape_markdown_v2(
                "Ich analysiere Kryptomaerkte ueber mehrere Timeframes und melde "
                "nachvollziehbare Signale. Ich fuehre keine Trades aus."
            )
            + "\n\n"
            + escape_markdown_v2("Starte mit /analyze BTCUSDT oder /help")
            + f"\n\n⚠️ {escape_markdown_v2(DISCLAIMER)}"
        )
        await self._reply(update, text)

    async def help(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._authorize(update, chat_id):
            return

        text = HELP_TEXT
        if self._access.is_admin(chat_id):
            text = f"{text}\n\n{ADMIN_HELP_TEXT}"
        await self._reply(update, text)

    async def status(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._authorize(update, chat_id):
            return

        provider_ok = await self._analysis.provider.health_check()
        lines = [
            f"*{escape_markdown_v2('Systemstatus')}*",
            "",
            escape_markdown_v2(f"Marktdaten ({self._settings.market_data_provider}): ")
            + ("erreichbar" if provider_ok else "nicht erreichbar"),
            escape_markdown_v2(
                f"LLM-Analyse: {'aktiv' if self._settings.llm_configured else 'inaktiv'}"
            ),
            escape_markdown_v2(
                f"Sentiment: {'aktiv' if self._settings.enable_sentiment else 'inaktiv'}"
            ),
            escape_markdown_v2(f"Timeframes: {', '.join(self._settings.timeframes)}"),
            escape_markdown_v2(f"Mindestscore: {self._settings.signal_min_score:.0f}"),
            escape_markdown_v2(f"Zeit (UTC): {utc_now().strftime('%Y-%m-%d %H:%M:%S')}"),
        ]
        await self._reply(update, "\n".join(lines))

    async def settings_command(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._authorize(update, chat_id):
            return

        cfg = self._settings
        lines = [
            f"*{escape_markdown_v2('Aktuelle Einstellungen')}*",
            "",
            escape_markdown_v2(f"Timeframes: {', '.join(cfg.timeframes)}"),
            escape_markdown_v2(f"Setup-Timeframe: {cfg.primary_timeframe}"),
            escape_markdown_v2(f"Mindestscore: {cfg.signal_min_score:.0f}/100"),
            escape_markdown_v2(f"Mindest-Chance-Risiko: {cfg.min_risk_reward_ratio:.2f}"),
            escape_markdown_v2(f"Max. Risiko je Trade: {cfg.max_risk_percent:.2f}%"),
            escape_markdown_v2(f"ATR-Multiplikator (Stop): {cfg.atr_multiplier:.2f}"),
            escape_markdown_v2(f"Cooldown: {cfg.signal_cooldown_minutes} Minuten"),
            escape_markdown_v2(f"Scan-Intervall: {cfg.scan_interval_minutes} Minuten"),
            escape_markdown_v2(f"LLM: {'aktiv' if cfg.llm_configured else 'inaktiv'}"),
            escape_markdown_v2(f"Backtesting: {'aktiv' if cfg.enable_backtesting else 'inaktiv'}"),
            escape_markdown_v2(
                f"Auto-Kalibrierung: {'aktiv' if cfg.enable_auto_calibration else 'inaktiv'}"
            ),
            "",
            f"⚠️ {escape_markdown_v2(DISCLAIMER)}",
        ]
        await self._reply(update, "\n".join(lines))

    # --- Analyse ----------------------------------------------------------

    async def analyze(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._run_analysis(update, context, compact=False)

    async def signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._run_analysis(update, context, compact=True)

    async def _run_analysis(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, *, compact: bool
    ) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._authorize(update, chat_id):
            return

        set_correlation_id()
        symbol = self._extract_symbol(context)
        if symbol is None:
            await self._reply(
                update,
                escape_markdown_v2("Bitte ein Symbol angeben, zum Beispiel: /analyze BTCUSDT"),
            )
            return

        await self._reply(
            update, escape_markdown_v2(f"Analysiere {symbol} ueber mehrere Timeframes ...")
        )

        try:
            async with session_scope() as session:
                outcome = await self._analysis.analyze(symbol, session=session, persist=True)

                formatter = format_signal_message if compact else format_analysis_message
                text = formatter(
                    outcome.result,
                    price_precision=outcome.price_precision,
                    display_timezone=self._settings.display_timezone,
                    llm_analysis=outcome.llm_analysis,
                )
                await self._reply_analysis(update, outcome, text)

                if not compact:
                    await self._reply(update, format_score_breakdown(outcome.result))

                if outcome.skipped_timeframes:
                    await self._reply(
                        update,
                        escape_markdown_v2(
                            "Hinweis: Diese Timeframes wurden wegen unzureichender "
                            f"Daten ausgelassen: {', '.join(outcome.skipped_timeframes)}"
                        ),
                    )
        except AlphaTradeOracleError as exc:
            await self._reply(update, escape_markdown_v2(f"Analyse nicht moeglich: {exc}"))
        except Exception as exc:
            logger.error("analyze_command_failed", symbol=symbol, error=str(exc), exc_info=True)
            await self._reply(
                update,
                escape_markdown_v2(
                    "Bei der Analyse ist ein unerwarteter Fehler aufgetreten. "
                    "Details stehen im Anwendungsprotokoll."
                ),
            )

    # --- Watchlist --------------------------------------------------------

    async def watch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._authorize(update, chat_id):
            return

        symbol = self._extract_symbol(context)
        if symbol is None:
            await self._reply(
                update, escape_markdown_v2("Bitte ein Symbol angeben, z. B. /watch BTCUSDT")
            )
            return

        try:
            info = await self._analysis.provider.get_symbol_info(symbol)
        except AlphaTradeOracleError as exc:
            await self._reply(update, escape_markdown_v2(str(exc)))
            return

        async with session_scope() as session:
            chat = await ChatRepository(session).get_or_create(
                chat_id, is_admin=self._access.is_admin(chat_id)
            )
            asset = await AssetRepository(session).get_or_create(info)
            _, created = await WatchlistRepository(session).add(chat.id, asset.id)

        message = (
            f"{symbol} wurde zur Watchlist hinzugefuegt."
            if created
            else f"{symbol} ist bereits auf der Watchlist."
        )
        await self._reply(update, escape_markdown_v2(message))

    async def unwatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._authorize(update, chat_id):
            return

        symbol = self._extract_symbol(context)
        if symbol is None:
            await self._reply(
                update, escape_markdown_v2("Bitte ein Symbol angeben, z. B. /unwatch BTCUSDT")
            )
            return

        async with session_scope() as session:
            chat = await ChatRepository(session).get_by_chat_id(chat_id)
            asset = await AssetRepository(session).get_by_symbol(symbol)
            removed = False
            if chat is not None and asset is not None:
                removed = await WatchlistRepository(session).remove(chat.id, asset.id)

        message = (
            f"{symbol} wurde von der Watchlist entfernt."
            if removed
            else f"{symbol} stand nicht auf der Watchlist."
        )
        await self._reply(update, escape_markdown_v2(message))

    async def watchlist(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._authorize(update, chat_id):
            return

        async with session_scope() as session:
            chat = await ChatRepository(session).get_by_chat_id(chat_id)
            entries = (
                await WatchlistRepository(session).list_for_chat(chat.id)
                if chat is not None
                else []
            )

        if not entries:
            await self._reply(
                update,
                escape_markdown_v2(
                    "Deine Watchlist ist leer. Fuege ein Symbol hinzu mit /watch BTCUSDT"
                ),
            )
            return

        lines = [f"*{escape_markdown_v2('Deine Watchlist')}*", ""]
        for entry, asset in entries:
            timeframes = entry.timeframes or ",".join(self._settings.timeframes)
            lines.append(escape_markdown_v2(f"• {asset.symbol} ({timeframes})"))
        await self._reply(update, "\n".join(lines))

    async def performance(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._authorize(update, chat_id):
            return

        async with session_scope() as session:
            summary = await SignalRepository(session).performance_summary(days=30)

        lines = [
            f"*{escape_markdown_v2('Signal-Auswertung (30 Tage)')}*",
            "",
            escape_markdown_v2(f"Signale erzeugt: {summary.get('signals_total', 0)}"),
            escape_markdown_v2(f"Signale versendet: {summary.get('signals_dispatched', 0)}"),
            escape_markdown_v2(f"Durchschnittsscore: {summary.get('average_score', 0)}"),
            escape_markdown_v2(
                f"Durchschnittliches Chance-Risiko: {summary.get('average_risk_reward', 0)}"
            ),
            escape_markdown_v2(
                f"Durchschn. Datenqualitaet: {summary.get('average_data_quality', 0)}"
            ),
        ]
        for direction in ("strong_long", "long", "neutral", "short", "strong_short", "no_trade"):
            count = summary.get(f"count_{direction}")
            if count:
                lines.append(escape_markdown_v2(f"{direction.upper()}: {count}"))

        lines += [
            "",
            escape_markdown_v2(
                "Die Auswertung bezieht sich auf die Signalproduktion, nicht auf "
                "realisierte Handelsergebnisse."
            ),
            "",
            f"⚠️ {escape_markdown_v2(DISCLAIMER)}",
        ]
        await self._reply(update, "\n".join(lines))

    async def paper(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._authorize(update, chat_id):
            return

        if self._paper is None or not self._settings.enable_paper_trading:
            await self._reply(
                update,
                escape_markdown_v2("Paper-Trading ist derzeit deaktiviert."),
            )
            return

        async with session_scope() as session:
            summary = await self._paper.summary(session)
            account = await self._paper.get_or_create_account(session)
            open_positions = await PaperRepository(session).list_open_positions(account.id)
            closed = await PaperRepository(session).list_closed(account.id, limit=5)

        lines = [
            f"*{escape_markdown_v2('Paper-Trading')}*",
            "",
            escape_markdown_v2(
                f"Equity: ${summary.equity:,.2f}  |  Cash: ${summary.cash_balance:,.2f}"
            ),
            escape_markdown_v2(
                f"Start: ${summary.initial_balance:,.2f}  |  "
                f"Realisiert: ${summary.realized_pnl:,.2f}"
            ),
            escape_markdown_v2(
                f"Offen: {summary.open_positions}  |  Margin: ${summary.open_margin:,.2f}"
            ),
            escape_markdown_v2(
                f"Geschlossen: {summary.closed_trades}  |  "
                f"Win-Rate: {summary.win_rate * 100:.0f}%  |  "
                f"PF: {summary.profit_factor:.2f}"
            ),
            escape_markdown_v2(
                f"Gesamt: {summary.total_r:+.2f}R  |  "
                f"Erwartung: {summary.expectancy_r:+.3f}R/Trade  |  "
                f"Gebuehren: {summary.fees_r:.2f}R  ({summary.r_trades} Trades mit 1R)"
            ),
            escape_markdown_v2(
                (
                    f"Margin je Trade: ${self._settings.paper_margin_per_trade:.0f} "
                    if self._settings.paper_risk_per_trade_usd <= 0
                    else f"Risiko je Trade: ${self._settings.paper_risk_per_trade_usd:.0f} "
                )
                + f"| Hebel {self._settings.paper_leverage:.0f}x "
                + f"| Notional-Cap ${self._settings.paper_max_notional_usd:.0f}"
            ),
        ]

        if open_positions:
            lines += ["", f"*{escape_markdown_v2('Offene Positionen')}*"]
            for pos in open_positions[:10]:
                lines.append(
                    escape_markdown_v2(
                        f"{pos.symbol} {pos.direction.upper()} @ {float(pos.entry_price):.4g} "
                        f"| SL {float(pos.current_stop):.4g} "
                        f"| rem {float(pos.remaining_quantity):.4g}"
                    )
                )

        if closed:
            lines += ["", f"*{escape_markdown_v2('Letzte Abschluesse')}*"]
            for pos in closed:
                lines.append(
                    escape_markdown_v2(
                        f"{pos.symbol} {pos.direction.upper()} "
                        f"PnL ${float(pos.realized_pnl):,.2f} "
                        f"({pos.exit_reason or '-'})"
                    )
                )

        lines += ["", f"⚠️ {escape_markdown_v2(DISCLAIMER)}"]
        await self._reply(update, "\n".join(lines))

    # --- Admin ------------------------------------------------------------

    async def admin_status(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._require_admin(update, chat_id):
            return

        async with session_scope() as session:
            jobs = await ScheduledJobRepository(session).list_all()

        lines = [f"*{escape_markdown_v2('Admin-Status')}*", ""]
        if not jobs:
            lines.append(escape_markdown_v2("Keine Hintergrundjobs registriert."))
        for job in jobs:
            lines.append(
                escape_markdown_v2(
                    f"{job.job_key}: {job.last_status or 'nie gelaufen'} | "
                    f"Laeufe: {job.run_count} | Intervall: {job.interval_seconds}s"
                )
            )
            if job.last_error:
                lines.append(f"  _{escape_markdown_v2(job.last_error[:150])}_")

        lines += [
            "",
            escape_markdown_v2(f"Erlaubte Chats: {len(self._settings.allowed_chat_ids)}"),
            escape_markdown_v2(f"Admin-Chats: {len(self._settings.admin_chat_ids)}"),
        ]
        await self._reply(update, "\n".join(lines))

    async def run_scan(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._require_admin(update, chat_id):
            return

        if self._scan is None:
            await self._reply(
                update, escape_markdown_v2("Der Scan-Service ist in diesem Prozess nicht aktiv.")
            )
            return

        await self._reply(update, escape_markdown_v2("Scan wird gestartet ..."))
        async with session_scope() as session:
            result = await self._scan.scan(session)

        lines = [
            f"*{escape_markdown_v2('Scan abgeschlossen')}*",
            "",
            escape_markdown_v2(f"Symbole geprueft: {result.symbols_scanned}"),
            escape_markdown_v2(f"Signale erzeugt: {result.signals_created}"),
            escape_markdown_v2(f"Signale versendet: {result.signals_dispatched}"),
            escape_markdown_v2(f"Signale unterdrueckt: {result.signals_suppressed}"),
        ]
        for symbol, detail in result.suppression_details[:10]:
            lines.append(escape_markdown_v2(f"  {symbol}: {detail}"))
        for symbol, error in result.failures[:5]:
            lines.append(escape_markdown_v2(f"  Fehler {symbol}: {error[:120]}"))
        await self._reply(update, "\n".join(lines))

    async def backtest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._require_admin(update, chat_id):
            return

        if self._backtest is None or not self._settings.enable_backtesting:
            await self._reply(update, escape_markdown_v2("Backtesting ist nicht aktiviert."))
            return

        args = list(context.args or [])
        if not args:
            await self._reply(
                update,
                escape_markdown_v2(
                    "Nutzung: /backtest BTCUSDT [TIMEFRAME] [TAGE], z. B. /backtest BTCUSDT 1h 180"
                ),
            )
            return

        symbol = args[0].upper()
        timeframe = args[1] if len(args) > 1 else self._settings.primary_timeframe
        try:
            days = int(args[2]) if len(args) > 2 else 180
        except ValueError:
            await self._reply(
                update, escape_markdown_v2("Die Anzahl der Tage muss eine Zahl sein.")
            )
            return

        end = utc_now()
        start = end - timedelta(days=max(1, days))

        await self._reply(
            update,
            escape_markdown_v2(
                f"Backtest {symbol} {timeframe} von {start.date()} bis {end.date()} laeuft ..."
            ),
        )

        try:
            async with session_scope() as session:
                report = await self._backtest.run(symbol, timeframe, start, end, session=session)
        except AlphaTradeOracleError as exc:
            await self._reply(update, escape_markdown_v2(f"Backtest fehlgeschlagen: {exc}"))
            return

        await self._reply(update, _format_backtest_report(report, symbol, timeframe))

    async def reload_config(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None or not await self._require_admin(update, chat_id):
            return

        from app.core.config import get_settings

        get_settings.cache_clear()
        reloaded = get_settings()
        self._settings = reloaded
        self._access = AccessControl(reloaded)

        await self._reply(
            update,
            escape_markdown_v2(
                "Konfiguration neu geladen. Hinweis: Verbindungen zu Datenbank, "
                "Redis und Marktdaten-Provider bleiben unveraendert und werden "
                "erst bei einem Neustart neu aufgebaut."
            ),
        )

    # --- Hilfsfunktionen --------------------------------------------------

    async def _authorize(self, update: Update, chat_id: int) -> bool:
        if self._access.is_allowed(chat_id):
            return True
        logger.warning("unauthorized_chat", chat_id=chat_id)
        await self._reply(update, escape_markdown_v2(self._access.denial_message(chat_id)))
        return False

    async def _require_admin(self, update: Update, chat_id: int) -> bool:
        if not await self._authorize(update, chat_id):
            return False
        if self._access.is_admin(chat_id):
            return True
        await self._reply(update, escape_markdown_v2(self._access.admin_denial_message()))
        return False

    async def _register_chat(self, chat_id: int, update: Update) -> None:
        chat = update.effective_chat
        async with session_scope() as session:
            await ChatRepository(session).get_or_create(
                chat_id,
                chat_type=chat.type if chat else "private",
                title=chat.title if chat else None,
                is_admin=self._access.is_admin(chat_id),
            )

    @staticmethod
    def _extract_symbol(context: ContextTypes.DEFAULT_TYPE) -> str | None:
        args = context.args or []
        if not args:
            return None
        candidate = args[0].upper().strip().replace("/", "").replace("-", "")
        return candidate or None

    @staticmethod
    async def _reply_analysis(update: Update, outcome: AnalysisOutcome, text: str) -> None:
        """Chart und Analyse-Text als Antwort senden."""
        message = update.effective_message
        if message is None:
            return

        async def send_text(payload: str) -> None:
            await BotHandlers._reply(update, payload)

        async def send_photo(photo: bytes, caption: str | None = None) -> None:
            await reply_photo(message, photo, caption=caption)

        await deliver_analysis_with_chart(
            outcome,
            text,
            send_text=send_text,
            send_photo=send_photo,
        )

    @staticmethod
    async def _reply(update: Update, text: str) -> None:
        """Antwort senden, bei Bedarf aufgeteilt."""
        message = update.effective_message
        if message is None:
            return
        for part in split_message(text):
            await message.reply_text(
                part, parse_mode=ParseMode.MARKDOWN_V2, disable_web_page_preview=True
            )


def _chat_id(update: Update) -> int | None:
    chat = update.effective_chat
    return chat.id if chat is not None else None


def _format_backtest_report(report: object, symbol: str, timeframe: str) -> str:
    metrics = getattr(report, "metrics", {}).get("overall", {})
    run_id = getattr(report, "run_id", None)

    lines = [
        f"*{escape_markdown_v2(f'Backtest {symbol} {timeframe}')}*",
        "",
        escape_markdown_v2(f"Trades: {int(metrics.get('trade_count', 0))}"),
        escape_markdown_v2(f"Trefferquote: {metrics.get('win_rate', 0.0) * 100:.1f}%"),
        escape_markdown_v2(
            f"Nettoergebnis: {metrics.get('net_profit', 0.0):.2f} "
            f"({metrics.get('net_profit_percent', 0.0):+.2f}%)"
        ),
        escape_markdown_v2(f"Profit Factor: {metrics.get('profit_factor', 0.0):.2f}"),
        escape_markdown_v2(f"Expectancy: {metrics.get('expectancy', 0.0):.2f}"),
        escape_markdown_v2(f"Max. Drawdown: {metrics.get('max_drawdown_percent', 0.0):.2f}%"),
        escape_markdown_v2(f"Sharpe: {metrics.get('sharpe_ratio', 0.0):.2f}"),
        escape_markdown_v2(f"Sortino: {metrics.get('sortino_ratio', 0.0):.2f}"),
        escape_markdown_v2(f"Gebuehren: {metrics.get('total_fees', 0.0):.2f}"),
    ]
    if run_id is not None:
        lines.append(escape_markdown_v2(f"Lauf-ID: {run_id}"))
    lines += [
        "",
        escape_markdown_v2("Historische Ergebnisse sind keine Zusage fuer zukuenftige Ergebnisse."),
        "",
        f"⚠️ {escape_markdown_v2(DISCLAIMER)}",
    ]
    return "\n".join(lines)
