"""ScanService — periodische Marktscans mit Deduplizierung und Zustellung.

Der Service ist so gebaut, dass ein Fehler bei einem Symbol den restlichen Scan
nicht abbricht: jedes Symbol wird einzeln behandelt und protokolliert.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.entry_blackout import is_in_utc_blackout
from app.core.enums import DeliveryStatus, EventSeverity, SuppressionReason
from app.core.errors import AlphaTradeOracleError
from app.core.logging import get_logger, set_correlation_id
from app.core.time import utc_now
from app.repositories.asset_repository import AssetRepository
from app.repositories.chat_repository import ChatRepository, WatchlistRepository
from app.repositories.event_repository import EventRepository
from app.repositories.signal_repository import SignalRepository
from app.services.analysis_service import AnalysisOutcome, AnalysisService
from app.services.paper_trading_service import PaperTradingService
from app.signals.dedup import SignalDeduplicator

logger = get_logger(__name__)


@dataclass
class ScanResult:
    """Ergebnis eines Scandurchlaufs."""

    symbols_scanned: int = 0
    signals_created: int = 0
    signals_dispatched: int = 0
    signals_suppressed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    suppression_details: list[tuple[str, str]] = field(default_factory=list)
    universe_mode: bool = False

    def as_summary(self) -> dict[str, int]:
        return {
            "symbols_scanned": self.symbols_scanned,
            "signals_created": self.signals_created,
            "signals_dispatched": self.signals_dispatched,
            "signals_suppressed": self.signals_suppressed,
            "failures": len(self.failures),
            "universe_mode": int(self.universe_mode),
        }


class SignalDispatcher:
    """Zustellprotokoll (Protocol-artig, hier als schmale Basisklasse).

    Die konkrete Telegram-Implementierung liegt in ``app/bot/notifier.py``. Der
    ScanService kennt Telegram damit nicht direkt.
    """

    async def dispatch(self, outcome: AnalysisOutcome) -> list[tuple[int, int | None, str | None]]:
        """Signal zustellen.

        Rueckgabe je Chat: ``(chat_db_id, message_id, error)``.
        """
        raise NotImplementedError


class ScanService:
    """Fuehrt Marktscans ueber Universe-Batch, Watchlist oder Standardsymbole aus."""

    def __init__(
        self,
        analysis_service: AnalysisService,
        deduplicator: SignalDeduplicator,
        *,
        dispatcher: SignalDispatcher | None = None,
        paper_trading: PaperTradingService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._analysis = analysis_service
        self._dedup = deduplicator
        self._dispatcher = dispatcher
        self._paper = paper_trading

    async def scan(
        self,
        session: AsyncSession,
        *,
        symbols: list[str] | None = None,
        dispatch: bool = True,
        use_universe: bool | None = None,
    ) -> ScanResult:
        """Relevante Symbole analysieren und passende Signale zustellen."""
        set_correlation_id()
        result = ScanResult()

        universe_mode = self._should_use_universe(symbols, use_universe)
        result.universe_mode = universe_mode

        targets = await self._resolve_targets(
            session, symbols=symbols, use_universe=universe_mode
        )
        if not targets:
            logger.info("scan_no_targets")
            return result

        logger.info(
            "scan_started",
            symbol_count=len(targets),
            universe_mode=universe_mode,
            symbols=targets[:20],
        )

        for symbol in targets:
            result.symbols_scanned += 1
            try:
                await self._scan_symbol(
                    session,
                    symbol,
                    result,
                    dispatch=dispatch,
                    universe_mode=universe_mode,
                )
            except AlphaTradeOracleError as exc:
                result.failures.append((symbol, str(exc)))
                logger.warning("scan_symbol_failed", symbol=symbol, error=str(exc))
            except Exception as exc:
                result.failures.append((symbol, str(exc)))
                logger.error("scan_symbol_error", symbol=symbol, error=str(exc), exc_info=True)
            finally:
                if universe_mode:
                    await AssetRepository(session).mark_scanned(symbol)

        await EventRepository(session).record(
            "market_scan_completed",
            f"Scan abgeschlossen: {result.signals_dispatched} von "
            f"{result.signals_created} Signalen versendet.",
            severity=EventSeverity.INFO if not result.failures else EventSeverity.WARNING,
            payload=result.as_summary(),
        )

        logger.info("scan_completed", **result.as_summary())
        return result

    def _should_use_universe(
        self, symbols: list[str] | None, use_universe: bool | None
    ) -> bool:
        if symbols is not None:
            return False
        if use_universe is not None:
            return use_universe
        return self._settings.enable_universe_scan

    async def _scan_symbol(
        self,
        session: AsyncSession,
        symbol: str,
        result: ScanResult,
        *,
        dispatch: bool,
        universe_mode: bool,
    ) -> None:
        outcome = await self._analysis.analyze(
            symbol,
            session=session,
            persist=True,
            use_llm=False if universe_mode else None,
        )
        result.signals_created += 1

        blackout_spec = self._settings.signal_entry_blackout_utc.strip()
        if (
            blackout_spec
            and outcome.result.direction.is_actionable
            and is_in_utc_blackout(utc_now(), blackout_spec)
        ):
            result.signals_suppressed += 1
            result.suppression_details.append((symbol, "entry_blackout_utc"))
            logger.info("signal_suppressed_entry_blackout", symbol=symbol)
            await self._record_suppression(session, outcome, SuppressionReason.ENTRY_BLACKOUT)
            return

        decision = await self._dedup.evaluate(
            outcome.result,
            min_score=self._settings.signal_min_score,
            short_max_score=self._settings.signal_short_max_score,
            min_risk_reward_ratio=self._settings.min_risk_reward_ratio,
            require_strong=self._settings.signal_require_strong,
        )

        signals = SignalRepository(session)

        if not decision.should_send:
            result.signals_suppressed += 1
            result.suppression_details.append((symbol, decision.detail))
            logger.info(
                "signal_suppressed",
                symbol=symbol,
                reason=decision.reason.value if decision.reason else "unknown",
                detail=decision.detail,
                score=outcome.result.score,
            )
            await self._record_suppression(session, outcome, decision.reason)
            return

        if self._paper is not None and self._paper.enabled:
            await self._paper.open_from_signal(session, outcome)

        if not dispatch:
            logger.info(
                "signal_ready_not_dispatched",
                symbol=symbol,
                score=outcome.result.score,
                universe_mode=universe_mode,
            )
            return

        if self._dispatcher is None:
            await self._dedup.record_dispatch(outcome.result)
            logger.info(
                "signal_processed_paper_only",
                symbol=symbol,
                score=outcome.result.score,
                universe_mode=universe_mode,
            )
            return

        deliveries = await self._dispatcher.dispatch(outcome)
        sent_any = False

        for chat_db_id, message_id, error in deliveries:
            if outcome.signal_id is None:
                continue
            if error is None:
                sent_any = True
                await signals.record_delivery(
                    outcome.signal_id,
                    chat_db_id,
                    status=DeliveryStatus.SENT,
                    message_id=message_id,
                )
            else:
                await signals.record_delivery(
                    outcome.signal_id,
                    chat_db_id,
                    status=DeliveryStatus.FAILED,
                    error_message=error,
                )

        if sent_any:
            result.signals_dispatched += 1
            if outcome.signal_id is not None:
                await signals.mark_dispatched(outcome.signal_id)
            # Erst nach erfolgreichem Versand merken, sonst wuerde ein
            # Zustellfehler den Cooldown auslösen und das Signal verschlucken.
            await self._dedup.record_dispatch(outcome.result)

    async def _record_suppression(
        self,
        session: AsyncSession,
        outcome: AnalysisOutcome,
        reason: SuppressionReason | None,
    ) -> None:
        """Unterdrueckung je Chat protokollieren, damit sie auswertbar bleibt."""
        if outcome.signal_id is None or reason is None:
            return

        chats = await ChatRepository(session).list_active_with_notifications()
        signals = SignalRepository(session)
        for chat in chats:
            await signals.record_delivery(
                outcome.signal_id,
                chat.id,
                status=DeliveryStatus.SUPPRESSED,
                suppression_reason=reason,
            )

    async def _resolve_targets(
        self,
        session: AsyncSession,
        *,
        symbols: list[str] | None,
        use_universe: bool,
    ) -> list[str]:
        """Ziele bestimmen: explizit, Universe-Batch oder Watchlist/Defaults."""
        if symbols is not None:
            return [symbol.upper().strip() for symbol in symbols if symbol.strip()]

        if use_universe:
            batch = await AssetRepository(session).list_universe_batch(
                self._settings.universe_scan_batch_size,
                max_rank=self._settings.universe_max_rank or None,
            )
            if batch:
                return [asset.symbol for asset in batch]
            logger.info("universe_batch_empty_falling_back")

        watched = await WatchlistRepository(session).distinct_watched_symbols()
        if watched:
            return watched
        return self._settings.symbols
