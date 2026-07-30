"""PaperTradingService — virtuelles Depot mit 33/33/34 Scale-out."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.enums import Confidence, ExitReason, MarketPhase, SignalDirection
from app.core.logging import get_logger
from app.core.time import ensure_utc, utc_now
from app.models.paper import PaperAccount, PaperFill, PaperPosition
from app.models.signal import Signal
from app.repositories.asset_repository import AssetRepository
from app.repositories.paper_repository import PaperRepository
from app.repositories.signal_repository import SignalRepository
from app.services.analysis_service import AnalysisOutcome
from app.signals.htf_breakout import (
    HtfArmResult,
    HtfBreakoutConfig,
    arm_htf_breakout,
    levels_from_entry_sl,
)
from app.signals.risk import DEFAULT_TP_MULTIPLIERS, RiskManager
from app.signals.types import RiskParameters, SignalResult

logger = get_logger(__name__)

SCALE_OUT_FRACTIONS = (Decimal("0.33333333"), Decimal("0.33333333"), Decimal("0.33333334"))


@dataclass
class PaperSummary:
    cash_balance: float
    initial_balance: float
    realized_pnl: float
    open_positions: int
    open_margin: float
    equity: float
    win_rate: float
    closed_trades: int
    profit_factor: float
    pending_positions: int = 0


@dataclass
class PaperBackfillResult:
    considered: int = 0
    opened: int = 0
    pending: int = 0
    skipped_existing: int = 0
    skipped_filters: int = 0
    skipped_cash: int = 0
    opened_symbols: list[str] | None = None

    def __post_init__(self) -> None:
        if self.opened_symbols is None:
            self.opened_symbols = []


@dataclass
class PaperRebuildResult:
    reset_positions: int = 0
    backfill: PaperBackfillResult | None = None
    htf_filled: int = 0
    htf_skipped: int = 0
    htf_still_pending: int = 0
    replayed: int = 0
    still_open: int = 0


@dataclass
class PendingResolveResult:
    filled: int = 0
    skipped: int = 0
    still_pending: int = 0


class PaperTradingService:
    """Oeffnet und verwaltet Paper-Positionen aus Signalen."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return self._settings.enable_paper_trading

    @property
    def htf_enabled(self) -> bool:
        return bool(self._settings.paper_htf_breakout_enabled)

    def _htf_config(self) -> HtfBreakoutConfig:
        return HtfBreakoutConfig(
            confirm_timeframe=self._settings.paper_htf_confirm_timeframe,
            lookback_bars=self._settings.paper_htf_lookback_bars,
            pending_days=self._settings.paper_htf_pending_days,
        )

    async def get_or_create_account(self, session: AsyncSession) -> PaperAccount:
        repo = PaperRepository(session)
        return await repo.get_or_create_account(
            name="default",
            initial_balance=Decimal(str(self._settings.paper_initial_balance)),
            margin_per_trade=Decimal(str(self._settings.paper_margin_per_trade)),
            leverage=self._settings.paper_leverage,
        )

    async def open_from_signal(
        self,
        session: AsyncSession,
        outcome: AnalysisOutcome,
        *,
        opened_at: datetime | None = None,
    ) -> PaperPosition | None:
        if not self.enabled:
            return None
        result = outcome.result
        if not result.direction.is_actionable or result.risk is None:
            return None

        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)

        existing = await repo.get_active_by_symbol(account.id, result.symbol)
        if existing is not None:
            logger.info(
                "paper_skip_already_open",
                symbol=result.symbol,
                status=existing.status,
            )
            return None

        if self.htf_enabled:
            return await self._open_pending_htf(
                session, account, outcome, opened_at=opened_at
            )

        margin = Decimal(str(self._settings.paper_margin_per_trade))
        if account.cash_balance < margin:
            logger.warning(
                "paper_insufficient_cash",
                cash=float(account.cash_balance),
                needed=float(margin),
            )
            return None

        leverage = Decimal(str(self._settings.paper_leverage))
        entry = Decimal(str(result.risk.entry_mid or result.reference_price))
        if entry <= 0:
            return None

        notional = margin * leverage
        quantity = notional / entry
        fee_rate = Decimal(str(self._settings.paper_fee_percent)) / Decimal("100")
        entry_fee = notional * fee_rate

        account.cash_balance -= margin + entry_fee
        now = opened_at or utc_now()

        position = PaperPosition(
            account_id=account.id,
            signal_id=outcome.signal_id,
            asset_id=outcome.asset_id,
            symbol=result.symbol.upper(),
            direction=result.direction.value,
            status="open",
            timeframe=result.primary_timeframe,
            entry_price=entry,
            stop_loss=Decimal(str(result.risk.stop_loss)),
            current_stop=Decimal(str(result.risk.stop_loss)),
            take_profit_1=Decimal(str(result.risk.take_profit_1)),
            take_profit_2=Decimal(str(result.risk.take_profit_2)),
            take_profit_3=Decimal(str(result.risk.take_profit_3)),
            initial_quantity=quantity,
            remaining_quantity=quantity,
            margin_used=margin,
            notional=notional,
            leverage=float(leverage),
            fees=entry_fee,
            signal_score=result.score,
            opened_at=now,
            expires_at=result.expires_at,
        )
        await repo.add_position(position)
        await repo.add_fill(
            PaperFill(
                position_id=position.id,
                reason="entry",
                price=entry,
                quantity=quantity,
                fee=entry_fee,
                pnl=Decimal("0"),
                filled_at=now,
            )
        )
        account.realized_pnl -= entry_fee

        logger.info(
            "paper_position_opened",
            symbol=position.symbol,
            direction=position.direction,
            margin=float(margin),
            notional=float(notional),
            quantity=float(quantity),
        )
        return position

    async def _open_pending_htf(
        self,
        session: AsyncSession,
        account: PaperAccount,
        outcome: AnalysisOutcome,
        *,
        opened_at: datetime | None = None,
    ) -> PaperPosition | None:
        """Pending-Entry anlegen; Fill erst nach 4h-Close-Bestaetigung."""
        result = outcome.result
        assert result.risk is not None
        repo = PaperRepository(session)
        leverage = Decimal(str(self._settings.paper_leverage))
        margin = Decimal(str(self._settings.paper_margin_per_trade))
        entry = Decimal(str(result.risk.entry_mid or result.reference_price))
        if entry <= 0:
            return None

        armed_at = ensure_utc(opened_at or result.created_at or utc_now())
        expires_at = armed_at + timedelta(days=self._settings.paper_htf_pending_days)

        notional = margin * leverage
        quantity = notional / entry
        stop = Decimal(str(result.risk.stop_loss))

        position = PaperPosition(
            account_id=account.id,
            signal_id=outcome.signal_id,
            asset_id=outcome.asset_id,
            symbol=result.symbol.upper(),
            direction=result.direction.value,
            status="pending",
            timeframe=result.primary_timeframe,
            entry_price=entry,
            stop_loss=stop,
            current_stop=stop,
            take_profit_1=Decimal(str(result.risk.take_profit_1)),
            take_profit_2=Decimal(str(result.risk.take_profit_2)),
            take_profit_3=Decimal(str(result.risk.take_profit_3)),
            initial_quantity=quantity,
            remaining_quantity=quantity,
            margin_used=Decimal("0"),
            notional=notional,
            leverage=float(leverage),
            fees=Decimal("0"),
            signal_score=result.score,
            opened_at=armed_at,
            expires_at=expires_at,
            notes=(
                f"htf_pending;orig_sl={float(stop)};"
                f"armed_at={armed_at.isoformat()};"
                f"confirm_tf={self._settings.paper_htf_confirm_timeframe}"
            ),
        )
        await repo.add_position(position)
        logger.info(
            "paper_position_pending_htf",
            symbol=position.symbol,
            direction=position.direction,
            armed_at=armed_at.isoformat(),
            expires_at=expires_at.isoformat(),
            orig_sl=float(stop),
        )
        return position

    async def resolve_pending_htf(
        self,
        session: AsyncSession,
        provider,
        *,
        end_time: datetime | None = None,
        historical: bool = False,
    ) -> PendingResolveResult:
        """Pending-Entries gegen 4h-Kerzen pruefen (Fill / Skip / weiter warten)."""
        out = PendingResolveResult()
        if not self.enabled or not self.htf_enabled:
            return out

        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)
        pending = await repo.list_pending_positions(account.id)
        if not pending:
            return out

        cfg = self._htf_config()
        cutoff = ensure_utc(end_time or utc_now())
        lookback_pad = timedelta(days=max(40, cfg.lookback_bars // 6 + 5))

        for position in pending:
            try:
                series = await provider.get_candles(
                    position.symbol,
                    cfg.confirm_timeframe,
                    limit=100_000,
                    start_time=ensure_utc(position.opened_at) - lookback_pad,
                    end_time=cutoff,
                )
                candles = (
                    list(series.candles)
                    if series is not None and not series.is_empty
                    else []
                )
            except Exception as exc:
                logger.warning(
                    "paper_htf_candles_failed",
                    symbol=position.symbol,
                    error=str(exc),
                )
                out.still_pending += 1
                continue

            arm = arm_htf_breakout(
                direction=position.direction,
                arm_time=position.opened_at,
                original_stop=float(position.stop_loss),
                candles_4h=candles,
                config=cfg,
            )
            if (
                arm.filled
                and arm.fill_price is not None
                and arm.fill_time is not None
                and arm.stop is not None
            ):
                activated = await self._activate_pending_htf(
                    session, account, position, arm
                )
                if activated:
                    out.filled += 1
                else:
                    out.skipped += 1
            elif arm.status == "pending" and not historical:
                out.still_pending += 1
            elif arm.status == "pending" and historical:
                if position.expires_at is not None and cutoff >= ensure_utc(
                    position.expires_at
                ):
                    await self._cancel_pending_htf(session, position, arm)
                    out.skipped += 1
                else:
                    out.still_pending += 1
            else:
                await self._cancel_pending_htf(session, position, arm)
                out.skipped += 1

        return out

    async def _activate_pending_htf(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        arm: HtfArmResult,
    ) -> bool:
        assert arm.fill_price is not None and arm.fill_time is not None and arm.stop is not None
        margin = Decimal(str(self._settings.paper_margin_per_trade))
        if account.cash_balance < margin:
            logger.warning(
                "paper_htf_activate_insufficient_cash",
                symbol=position.symbol,
                cash=float(account.cash_balance),
            )
            await self._cancel_pending_htf(
                session,
                position,
                HtfArmResult(
                    status="skipped_cash",
                    note="insufficient_cash_at_fill",
                    level=arm.level,
                ),
            )
            return False

        leverage = Decimal(str(self._settings.paper_leverage))
        entry = Decimal(str(arm.fill_price))
        stop = Decimal(str(arm.stop))
        is_long = SignalDirection(position.direction).is_long
        tp1, tp2, tp3 = levels_from_entry_sl(entry, stop, is_long=is_long)

        notional = margin * leverage
        quantity = notional / entry if entry > 0 else Decimal("0")
        fee_rate = Decimal(str(self._settings.paper_fee_percent)) / Decimal("100")
        entry_fee = notional * fee_rate
        fill_time = ensure_utc(arm.fill_time)

        account.cash_balance -= margin + entry_fee
        account.realized_pnl -= entry_fee

        position.status = "open"
        position.entry_price = entry
        position.stop_loss = stop
        position.current_stop = stop
        position.take_profit_1 = tp1
        position.take_profit_2 = tp2
        position.take_profit_3 = tp3
        position.initial_quantity = quantity
        position.remaining_quantity = quantity
        position.margin_used = margin
        position.notional = notional
        position.fees = entry_fee
        position.opened_at = fill_time
        position.expires_at = fill_time + timedelta(
            days=self._settings.paper_htf_pending_days
        )
        position.notes = (
            f"htf_filled;level={arm.level};bars={arm.bars_waited};"
            f"note={arm.note};fill={float(entry)};stop={float(stop)}"
        )

        repo = PaperRepository(session)
        await repo.add_fill(
            PaperFill(
                position_id=position.id,
                reason="entry",
                price=entry,
                quantity=quantity,
                fee=entry_fee,
                pnl=Decimal("0"),
                filled_at=fill_time,
            )
        )
        logger.info(
            "paper_position_htf_filled",
            symbol=position.symbol,
            entry=float(entry),
            stop=float(stop),
            level=arm.level,
            bars_waited=arm.bars_waited,
            fill_time=fill_time.isoformat(),
        )
        return True

    async def _cancel_pending_htf(
        self,
        session: AsyncSession,
        position: PaperPosition,
        arm: HtfArmResult,
    ) -> None:
        position.status = "cancelled"
        position.closed_at = utc_now()
        position.exit_reason = ExitReason.HTF_SKIPPED.value
        position.remaining_quantity = Decimal("0")
        position.margin_used = Decimal("0")
        position.notes = (
            f"htf_skipped;status={arm.status};note={arm.note};"
            f"level={arm.level};bars={arm.bars_waited}"
        )
        logger.info(
            "paper_position_htf_skipped",
            symbol=position.symbol,
            status=arm.status,
            note=arm.note,
            bars_waited=arm.bars_waited,
        )

    async def backfill_from_signals(
        self,
        session: AsyncSession,
        *,
        since: datetime,
        dispatched_only: bool = False,
        one_per_symbol: bool = True,
    ) -> PaperBackfillResult:
        """Qualifizierende Signale ab ``since`` als Paper-Trades nachziehen."""
        result = PaperBackfillResult()
        if not self.enabled:
            return result

        signals = await SignalRepository(session).list_since(
            since,
            actionable_only=True,
            dispatched_only=dispatched_only,
            limit=1000,
        )
        if not signals:
            return result

        asset_ids = list({signal.asset_id for signal in signals})
        symbols_by_id = await AssetRepository(session).get_symbols_by_ids(asset_ids)

        seen_symbols: set[str] = set()
        # one_per_symbol: neueste zuerst; sonst chronologisch (Retro-Replay).
        ordered = sorted(
            signals,
            key=lambda s: s.created_at,
            reverse=one_per_symbol,
        )

        for signal in ordered:
            result.considered += 1
            symbol = symbols_by_id.get(signal.asset_id)
            if not symbol:
                result.skipped_filters += 1
                continue
            symbol = symbol.upper()
            if one_per_symbol and symbol in seen_symbols:
                result.skipped_filters += 1
                continue
            if not self._passes_paper_gates(signal):
                result.skipped_filters += 1
                continue

            position = await self.open_from_stored_signal(
                session, signal, symbol=symbol, extend_expiry=not self.htf_enabled
            )
            if position is None:
                account = await self.get_or_create_account(session)
                existing = await PaperRepository(session).get_active_by_symbol(
                    account.id, symbol
                )
                if existing is not None:
                    result.skipped_existing += 1
                else:
                    result.skipped_cash += 1
                continue

            seen_symbols.add(symbol)
            result.opened += 1
            if position.status == "pending":
                result.pending += 1
            assert result.opened_symbols is not None
            result.opened_symbols.append(symbol)

        return result

    def _passes_paper_gates(self, signal: Signal) -> bool:
        try:
            direction = SignalDirection(signal.direction)
        except ValueError:
            return False
        if not direction.is_actionable:
            return False
        if self._settings.signal_require_strong and direction not in {
            SignalDirection.STRONG_LONG,
            SignalDirection.STRONG_SHORT,
        }:
            return False
        if direction.is_long and float(signal.score) < self._settings.signal_min_score:
            return False
        if direction.is_short and float(signal.score) > self._settings.signal_short_max_score:
            return False
        if signal.stop_loss is None or signal.take_profit_1 is None:
            return False
        if signal.take_profit_2 is None or signal.take_profit_3 is None:
            return False
        rr = float(signal.risk_reward_ratio or 0.0)
        if rr < self._settings.min_risk_reward_ratio:
            return False
        if float(signal.data_quality) < 60.0:
            return False
        return True

    async def open_from_stored_signal(
        self,
        session: AsyncSession,
        signal: Signal,
        *,
        symbol: str,
        extend_expiry: bool = False,
    ) -> PaperPosition | None:
        """Paper-Position aus einem persistierten Signal oeffnen."""
        if not self.enabled:
            return None
        try:
            direction = SignalDirection(signal.direction)
        except ValueError:
            return None
        if not direction.is_actionable:
            return None
        if signal.stop_loss is None or signal.take_profit_1 is None:
            return None
        if signal.take_profit_2 is None or signal.take_profit_3 is None:
            return None

        entry_low = float(signal.entry_low or signal.reference_price)
        entry_high = float(signal.entry_high or signal.reference_price)
        entry_mid = (entry_low + entry_high) / 2.0
        stop_loss = float(signal.stop_loss)
        tp1, tp2, tp3 = RiskManager.targets_from_stop(
            entry_mid,
            stop_loss,
            is_long=direction.is_long,
            multipliers=DEFAULT_TP_MULTIPLIERS,
        )
        stop_distance = abs(entry_mid - stop_loss)
        rr = abs(tp2 - entry_mid) / stop_distance if stop_distance > 0 else 0.0
        risk = RiskParameters(
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            risk_reward_ratio=rr,
            risk_percent=float(signal.risk_percent or 0.0),
            suggested_position_size=float(signal.suggested_position_size or 0.0),
            stop_distance_percent=(stop_distance / entry_mid * 100.0) if entry_mid else 0.0,
            invalidation_note=signal.invalidation_note or "",
        )
        expires_at = signal.expires_at
        if extend_expiry:
            floor = utc_now() + timedelta(hours=4)
            if expires_at < floor:
                expires_at = floor

        result = SignalResult(
            symbol=symbol.upper(),
            created_at=signal.created_at,
            expires_at=expires_at,
            direction=direction,
            score=float(signal.score),
            confidence=Confidence(signal.confidence),
            market_phase=MarketPhase(signal.market_phase),
            primary_timeframe=signal.primary_timeframe,
            analyzed_timeframes=[
                part.strip()
                for part in (signal.analyzed_timeframes or "").split(",")
                if part.strip()
            ],
            reference_price=float(signal.reference_price),
            data_quality=float(signal.data_quality),
            components=[],
            assessments={},
            risk=risk,
        )
        result.fingerprint = signal.fingerprint
        outcome = AnalysisOutcome(
            result=result,
            price_precision=8,
            signal_id=signal.id,
            asset_id=signal.asset_id,
        )
        return await self.open_from_signal(
            session, outcome, opened_at=signal.created_at
        )

    async def rebuild_from_signals(
        self,
        session: AsyncSession,
        *,
        since: datetime,
        provider,
        providers: list | None = None,
        dispatched_only: bool = False,
        one_per_symbol: bool = True,
    ) -> PaperRebuildResult:
        """Paper-Ledger leeren, HTF-Entries aufloesen und per Kerzen replayen."""
        from app.scheduler.jobs import _collect_prices

        out = PaperRebuildResult()
        if not self.enabled:
            return out

        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)
        out.reset_positions = await repo.reset_ledger(account)

        if self.htf_enabled and not one_per_symbol:
            out.backfill = await self._rebuild_htf_from_signal_stream(
                session,
                account,
                provider,
                since=since,
                dispatched_only=dispatched_only,
                out=out,
            )
        else:
            out.backfill = await self.backfill_from_signals(
                session,
                since=since,
                dispatched_only=dispatched_only,
                one_per_symbol=one_per_symbol,
            )
            if self.htf_enabled:
                resolve = await self.resolve_pending_htf(
                    session, provider, end_time=utc_now(), historical=True
                )
                out.htf_filled = resolve.filled
                out.htf_skipped = resolve.skipped
                out.htf_still_pending = resolve.still_pending

            positions = await repo.list_positions(account.id)
            for position in positions:
                if position.status != "open":
                    continue
                try:
                    series = await provider.get_candles(
                        position.symbol,
                        position.timeframe or "1h",
                        limit=100_000,
                        start_time=position.opened_at,
                        end_time=utc_now(),
                    )
                    await self._replay_bars(session, account, position, series.candles)
                    out.replayed += 1
                except Exception as exc:
                    logger.warning(
                        "paper_rebuild_replay_failed",
                        symbol=position.symbol,
                        error=str(exc),
                    )

        still_open = await repo.list_open_positions(account.id)
        out.still_open = len(still_open)
        if still_open:
            symbols = [p.symbol for p in still_open]
            prices = await _collect_prices(provider, symbols, providers=providers)
            await self.update_open_positions(session, prices)
            still_open = await repo.list_open_positions(account.id)
            out.still_open = len(still_open)

        return out

    async def _rebuild_htf_from_signal_stream(
        self,
        session: AsyncSession,
        account: PaperAccount,
        provider,
        *,
        since: datetime,
        dispatched_only: bool,
        out: PaperRebuildResult,
    ) -> PaperBackfillResult:
        """Signale chronologisch: pending -> HTF-Fill/Skip -> Exit-Replay -> naechstes."""
        backfill = PaperBackfillResult()
        signals = await SignalRepository(session).list_since(
            since,
            actionable_only=True,
            dispatched_only=dispatched_only,
            limit=5000,
        )
        if not signals:
            return backfill

        asset_ids = list({signal.asset_id for signal in signals})
        symbols_by_id = await AssetRepository(session).get_symbols_by_ids(asset_ids)
        cfg = self._htf_config()
        lookback_pad = timedelta(days=max(40, cfg.lookback_bars // 6 + 5))
        cutoff = utc_now()
        ordered = sorted(signals, key=lambda s: s.created_at)

        for signal in ordered:
            backfill.considered += 1
            symbol = symbols_by_id.get(signal.asset_id)
            if not symbol:
                backfill.skipped_filters += 1
                continue
            symbol = symbol.upper()
            if not self._passes_paper_gates(signal):
                backfill.skipped_filters += 1
                continue

            existing = await PaperRepository(session).get_active_by_symbol(account.id, symbol)
            if existing is not None:
                backfill.skipped_existing += 1
                continue

            position = await self.open_from_stored_signal(
                session, signal, symbol=symbol, extend_expiry=False
            )
            if position is None:
                backfill.skipped_cash += 1
                continue

            backfill.opened += 1
            assert backfill.opened_symbols is not None
            if symbol not in backfill.opened_symbols:
                backfill.opened_symbols.append(symbol)

            if position.status != "pending":
                continue
            backfill.pending += 1

            try:
                series_4h = await provider.get_candles(
                    symbol,
                    cfg.confirm_timeframe,
                    limit=100_000,
                    start_time=ensure_utc(position.opened_at) - lookback_pad,
                    end_time=cutoff,
                )
                candles_4h = (
                    list(series_4h.candles)
                    if series_4h is not None and not series_4h.is_empty
                    else []
                )
            except Exception as exc:
                logger.warning("paper_htf_candles_failed", symbol=symbol, error=str(exc))
                await self._cancel_pending_htf(
                    session,
                    position,
                    HtfArmResult(status="skipped_no_history", note=str(exc)),
                )
                out.htf_skipped += 1
                continue

            arm = arm_htf_breakout(
                direction=position.direction,
                arm_time=position.opened_at,
                original_stop=float(position.stop_loss),
                candles_4h=candles_4h,
                config=cfg,
            )
            if (
                arm.filled
                and arm.fill_price is not None
                and arm.fill_time is not None
                and arm.stop is not None
            ):
                ok = await self._activate_pending_htf(session, account, position, arm)
                if not ok:
                    out.htf_skipped += 1
                    continue
                out.htf_filled += 1
                try:
                    series = await provider.get_candles(
                        symbol,
                        position.timeframe or "1h",
                        limit=100_000,
                        start_time=position.opened_at,
                        end_time=cutoff,
                    )
                    await self._replay_bars(session, account, position, series.candles)
                    out.replayed += 1
                except Exception as exc:
                    logger.warning(
                        "paper_rebuild_replay_failed",
                        symbol=symbol,
                        error=str(exc),
                    )
            elif arm.status == "pending":
                if position.expires_at is not None and cutoff >= ensure_utc(position.expires_at):
                    await self._cancel_pending_htf(session, position, arm)
                    out.htf_skipped += 1
                else:
                    out.htf_still_pending += 1
            else:
                await self._cancel_pending_htf(session, position, arm)
                out.htf_skipped += 1

        return backfill

    async def _replay_bars(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        bars,
    ) -> None:
        """OHLC-Replay: Stop hat Vorrang, danach TPs in Reihenfolge."""
        if not bars:
            return
        for candle in bars:
            if position.status != "open":
                break
            when = getattr(candle, "open_time", None) or getattr(candle, "timestamp", None)
            if when is None:
                when = utc_now()
            high = float(candle.high)
            low = float(candle.low)
            close = float(candle.close)
            is_long = SignalDirection(position.direction).is_long
            stop = float(position.current_stop)

            stop_hit = low <= stop if is_long else high >= stop
            if stop_hit:
                await self._close_remaining(
                    session,
                    account,
                    position,
                    price=stop,
                    reason=ExitReason.STOP_LOSS,
                    when=when,
                )
                break

            await self._apply_price(
                session,
                account,
                position,
                price=high if is_long else low,
                when=when,
                check_stop=False,
            )
            if position.status != "open":
                break

            if (
                position.expires_at is not None
                and when >= position.expires_at
                and position.remaining_quantity > 0
            ):
                await self._close_remaining(
                    session,
                    account,
                    position,
                    price=close,
                    reason=ExitReason.EXPIRED,
                    when=when,
                )
                break

    async def update_open_positions(
        self, session: AsyncSession, prices: dict[str, float]
    ) -> list[PaperPosition]:
        """Offene Positionen gegen aktuelle Preise pruefen (SL/TP Scale-out)."""
        if not self.enabled:
            return []

        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)
        open_positions = await repo.list_open_positions(account.id)
        updated: list[PaperPosition] = []

        for position in open_positions:
            price = prices.get(position.symbol.upper())
            if price is None:
                continue
            changed = await self._apply_price(session, account, position, float(price))
            if changed:
                updated.append(position)

        return updated

    async def summary(
        self, session: AsyncSession, prices: dict[str, float] | None = None
    ) -> PaperSummary:
        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)
        open_positions = await repo.list_open_positions(account.id)
        pending_positions = await repo.list_pending_positions(account.id)
        closed = await repo.list_closed(account.id, limit=500)

        open_margin = sum((float(p.margin_used) for p in open_positions), 0.0)
        unrealized = 0.0
        if prices:
            for pos in open_positions:
                mark = prices.get(pos.symbol.upper())
                if mark is None:
                    continue
                direction = 1.0 if SignalDirection(pos.direction).is_long else -1.0
                unrealized += (
                    (mark - float(pos.entry_price))
                    * float(pos.remaining_quantity)
                    * direction
                )

        wins = [float(p.realized_pnl) for p in closed if float(p.realized_pnl) > 0]
        losses = [abs(float(p.realized_pnl)) for p in closed if float(p.realized_pnl) < 0]
        win_rate = (len(wins) / len(closed)) if closed else 0.0
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        profit_factor = (
            (gross_profit / gross_loss)
            if gross_loss > 0
            else (0.0 if gross_profit == 0 else 99.0)
        )

        equity = float(account.cash_balance) + open_margin + unrealized
        return PaperSummary(
            cash_balance=float(account.cash_balance),
            initial_balance=float(account.initial_balance),
            realized_pnl=float(account.realized_pnl),
            open_positions=len(open_positions),
            open_margin=open_margin,
            equity=equity,
            win_rate=win_rate,
            closed_trades=len(closed),
            profit_factor=profit_factor,
            pending_positions=len(pending_positions),
        )

    async def _apply_price(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        price: float,
        *,
        when=None,
        check_stop: bool = True,
    ) -> bool:
        is_long = SignalDirection(position.direction).is_long
        stop = float(position.current_stop)
        changed = False
        now = when or utc_now()

        if check_stop:
            stop_hit = price <= stop if is_long else price >= stop
            if stop_hit:
                await self._close_remaining(
                    session,
                    account,
                    position,
                    price=stop,
                    reason=ExitReason.STOP_LOSS,
                    when=now,
                )
                return True

        levels = (
            (
                not position.tp1_filled,
                float(position.take_profit_1),
                ExitReason.TAKE_PROFIT_1,
                SCALE_OUT_FRACTIONS[0],
                1,
            ),
            (
                not position.tp2_filled,
                float(position.take_profit_2),
                ExitReason.TAKE_PROFIT_2,
                SCALE_OUT_FRACTIONS[1],
                2,
            ),
            (
                not position.tp3_filled,
                float(position.take_profit_3),
                ExitReason.TAKE_PROFIT_3,
                SCALE_OUT_FRACTIONS[2],
                3,
            ),
        )
        for pending, tp, reason, fraction, level in levels:
            if not pending:
                continue
            hit = price >= tp if is_long else price <= tp
            if not hit:
                break
            qty = min(
                Decimal(str(float(position.initial_quantity) * float(fraction))),
                position.remaining_quantity,
            )
            if level == 3:
                qty = position.remaining_quantity
            await self._reduce(
                session, account, position, quantity=qty, price=tp, reason=reason, when=now
            )
            if level == 1:
                position.tp1_filled = True
                if self._settings.paper_move_stop_to_breakeven:
                    position.current_stop = position.entry_price
            elif level == 2:
                position.tp2_filled = True
            else:
                position.tp3_filled = True
            changed = True
            if position.remaining_quantity <= Decimal("0"):
                break

        if (
            check_stop
            and position.status == "open"
            and position.expires_at is not None
            and now >= position.expires_at
            and position.remaining_quantity > 0
        ):
            await self._close_remaining(
                session, account, position, price=price, reason=ExitReason.EXPIRED, when=now
            )
            return True

        return changed

    async def _reduce(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        *,
        quantity: Decimal,
        price: float,
        reason: ExitReason,
        when,
    ) -> None:
        if quantity <= 0 or position.remaining_quantity <= 0:
            return

        qty = min(quantity, position.remaining_quantity)
        direction = (
            Decimal("1") if SignalDirection(position.direction).is_long else Decimal("-1")
        )
        exit_price = Decimal(str(price))
        gross = (exit_price - position.entry_price) * qty * direction
        fee_rate = Decimal(str(self._settings.paper_fee_percent)) / Decimal("100")
        fee = exit_price * qty * fee_rate
        net = gross - fee

        share = qty / position.initial_quantity if position.initial_quantity else Decimal("0")
        margin_release = position.margin_used * share

        position.remaining_quantity -= qty
        position.margin_used = max(Decimal("0"), position.margin_used - margin_release)
        position.realized_pnl += net
        position.fees += fee
        position.exit_reason = reason.value
        account.cash_balance += margin_release + net
        account.realized_pnl += net

        repo = PaperRepository(session)
        await repo.add_fill(
            PaperFill(
                position_id=position.id,
                reason=reason.value,
                price=exit_price,
                quantity=qty,
                fee=fee,
                pnl=net,
                filled_at=when,
            )
        )

        if position.remaining_quantity <= Decimal("0.00000001"):
            position.remaining_quantity = Decimal("0")
            position.status = "closed"
            position.closed_at = when
            position.margin_used = Decimal("0")
            logger.info(
                "paper_position_closed",
                symbol=position.symbol,
                reason=reason.value,
                pnl=float(position.realized_pnl),
            )

    async def _close_remaining(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        *,
        price: float,
        reason: ExitReason,
        when,
    ) -> None:
        await self._reduce(
            session,
            account,
            position,
            quantity=position.remaining_quantity,
            price=price,
            reason=reason,
            when=when,
        )
