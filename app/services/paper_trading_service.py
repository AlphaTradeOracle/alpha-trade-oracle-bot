"""PaperTradingService — virtuelles Depot mit 33/33/34 Scale-out."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.enums import ExitReason, SignalDirection
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.paper import PaperAccount, PaperFill, PaperPosition
from app.repositories.paper_repository import PaperRepository
from app.services.analysis_service import AnalysisOutcome

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


class PaperTradingService:
    """Oeffnet und verwaltet Paper-Positionen aus Signalen."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return self._settings.enable_paper_trading

    async def get_or_create_account(self, session: AsyncSession) -> PaperAccount:
        repo = PaperRepository(session)
        return await repo.get_or_create_account(
            name="default",
            initial_balance=Decimal(str(self._settings.paper_initial_balance)),
            margin_per_trade=Decimal(str(self._settings.paper_margin_per_trade)),
            leverage=self._settings.paper_leverage,
        )

    async def open_from_signal(
        self, session: AsyncSession, outcome: AnalysisOutcome
    ) -> PaperPosition | None:
        if not self.enabled:
            return None
        result = outcome.result
        if not result.direction.is_actionable or result.risk is None:
            return None

        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)

        existing = await repo.get_open_by_symbol(account.id, result.symbol)
        if existing is not None:
            logger.info("paper_skip_already_open", symbol=result.symbol)
            return None

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
        now = utc_now()

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

    async def summary(self, session: AsyncSession, prices: dict[str, float] | None = None) -> PaperSummary:
        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)
        open_positions = await repo.list_open_positions(account.id)
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
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (0.0 if gross_profit == 0 else 99.0)

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
        )

    async def _apply_price(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        price: float,
    ) -> bool:
        is_long = SignalDirection(position.direction).is_long
        stop = float(position.current_stop)
        changed = False
        now = utc_now()

        stop_hit = price <= stop if is_long else price >= stop
        if stop_hit:
            await self._close_remaining(
                session, account, position, price=stop, reason=ExitReason.STOP_LOSS, when=now
            )
            return True

        levels = (
            (not position.tp1_filled, float(position.take_profit_1), ExitReason.TAKE_PROFIT_1, SCALE_OUT_FRACTIONS[0], 1),
            (not position.tp2_filled, float(position.take_profit_2), ExitReason.TAKE_PROFIT_2, SCALE_OUT_FRACTIONS[1], 2),
            (not position.tp3_filled, float(position.take_profit_3), ExitReason.TAKE_PROFIT_3, SCALE_OUT_FRACTIONS[2], 3),
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
            position.status == "open"
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
        direction = Decimal("1") if SignalDirection(position.direction).is_long else Decimal("-1")
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
