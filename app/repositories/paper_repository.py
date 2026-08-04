"""Repository fuer Paper-Trading-Konten und Positionen."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.paper import PaperAccount, PaperFill, PaperPosition
from app.models.signal import Signal


class PaperRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_account(
        self,
        *,
        name: str = "default",
        initial_balance: Decimal,
        margin_per_trade: Decimal,
        leverage: float,
    ) -> PaperAccount:
        result = await self._session.execute(
            select(PaperAccount).where(PaperAccount.name == name)
        )
        account = result.scalar_one_or_none()
        if account is not None:
            # Keep cash/initial intact; sync trade sizing from settings.
            account.margin_per_trade = margin_per_trade
            account.leverage = leverage
            return account

        account = PaperAccount(
            name=name,
            currency="USDT",
            initial_balance=initial_balance,
            cash_balance=initial_balance,
            realized_pnl=Decimal("0"),
            margin_per_trade=margin_per_trade,
            leverage=leverage,
            is_active=True,
        )
        self._session.add(account)
        await self._session.flush()
        return account

    async def reset_ledger(self, account: PaperAccount) -> int:
        """Alle Positionen/Fills loeschen und Cash auf Initial zuruecksetzen."""
        result = await self._session.execute(
            select(PaperPosition.id).where(PaperPosition.account_id == account.id)
        )
        position_ids = list(result.scalars().all())
        deleted = len(position_ids)
        if position_ids:
            await self._session.execute(
                delete(PaperFill).where(PaperFill.position_id.in_(position_ids))
            )
            await self._session.execute(
                delete(PaperPosition).where(PaperPosition.account_id == account.id)
            )
        account.cash_balance = account.initial_balance
        account.realized_pnl = Decimal("0")
        await self._session.flush()
        return deleted

    async def list_positions(self, account_id: int) -> list[PaperPosition]:
        result = await self._session.execute(
            select(PaperPosition)
            .where(PaperPosition.account_id == account_id)
            .options(selectinload(PaperPosition.fills))
            .order_by(PaperPosition.opened_at.asc())
        )
        return list(result.scalars())

    async def get_account(self, name: str = "default") -> PaperAccount | None:
        result = await self._session.execute(
            select(PaperAccount).where(PaperAccount.name == name)
        )
        return result.scalar_one_or_none()

    async def list_open_positions(self, account_id: int) -> list[PaperPosition]:
        result = await self._session.execute(
            select(PaperPosition)
            .where(
                PaperPosition.account_id == account_id,
                PaperPosition.status == "open",
            )
            .options(selectinload(PaperPosition.fills))
            .order_by(PaperPosition.opened_at.desc())
        )
        return list(result.scalars())

    async def list_filled_open_at(
        self, account_id: int, at: datetime
    ) -> list[PaperPosition]:
        """Filled positions whose open window covers ``at`` (as-of book).

        Includes ``status=open`` and already-``closed`` rows that were still live
        at ``at``. Pending/cancelled never-filled rows are excluded. Needed for
        rebuild: trades are replayed to completion before the next signal, so a
        plain ``status==open`` query under-counts concurrency at historical fills.
        """
        result = await self._session.execute(
            select(PaperPosition)
            .where(
                PaperPosition.account_id == account_id,
                PaperPosition.opened_at.is_not(None),
                PaperPosition.opened_at <= at,
                or_(
                    PaperPosition.status == "open",
                    and_(
                        PaperPosition.status == "closed",
                        PaperPosition.closed_at.is_not(None),
                        PaperPosition.closed_at > at,
                    ),
                ),
            )
            .options(selectinload(PaperPosition.fills))
            .order_by(PaperPosition.opened_at.asc())
        )
        return list(result.scalars())

    async def list_pending_positions(self, account_id: int) -> list[PaperPosition]:
        result = await self._session.execute(
            select(PaperPosition)
            .where(
                PaperPosition.account_id == account_id,
                PaperPosition.status == "pending",
            )
            .options(selectinload(PaperPosition.fills))
            .order_by(PaperPosition.opened_at.asc())
        )
        return list(result.scalars())

    async def get_open_by_symbol(self, account_id: int, symbol: str) -> PaperPosition | None:
        result = await self._session.execute(
            select(PaperPosition).where(
                PaperPosition.account_id == account_id,
                PaperPosition.symbol == symbol.upper(),
                PaperPosition.status == "open",
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_symbol(self, account_id: int, symbol: str) -> PaperPosition | None:
        """Offene oder pending Position fuer Symbol (Symbol-Sperre)."""
        result = await self._session.execute(
            select(PaperPosition).where(
                PaperPosition.account_id == account_id,
                PaperPosition.symbol == symbol.upper(),
                PaperPosition.status.in_(("open", "pending")),
            )
        )
        return result.scalar_one_or_none()

    async def is_symbol_busy_at(
        self, account_id: int, symbol: str, at: datetime
    ) -> bool:
        """True wenn das Symbol zum Zeitpunkt ``at`` schon einen Trade hatte.

        Start = Signalzeit (``signals.created_at``), Fallback ``opened_at``.
        Nach Retest-Fill wird ``opened_at`` auf die Fill-Zeit gesetzt — ohne
        Signalzeit wuerde ein zweites Signal zwischen Arm und Fill durchrutschen.
        Ende = ``closed_at`` bzw. offen bei open/pending.
        """
        start_at = func.coalesce(Signal.created_at, PaperPosition.opened_at)
        result = await self._session.execute(
            select(PaperPosition.id)
            .outerjoin(Signal, Signal.id == PaperPosition.signal_id)
            .where(
                PaperPosition.account_id == account_id,
                PaperPosition.symbol == symbol.upper(),
                start_at <= at,
                or_(
                    PaperPosition.status.in_(("open", "pending")),
                    and_(
                        PaperPosition.status.in_(("closed", "cancelled")),
                        PaperPosition.closed_at.is_not(None),
                        PaperPosition.closed_at > at,
                    ),
                ),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def list_recent_closed_by_symbol(
        self, account_id: int, symbol: str, *, limit: int = 2
    ) -> list[PaperPosition]:
        result = await self._session.execute(
            select(PaperPosition)
            .where(
                PaperPosition.account_id == account_id,
                PaperPosition.symbol == symbol.upper(),
                PaperPosition.status == "closed",
            )
            .order_by(PaperPosition.closed_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def list_closed(self, account_id: int, *, limit: int = 20) -> list[PaperPosition]:
        result = await self._session.execute(
            select(PaperPosition)
            .where(
                PaperPosition.account_id == account_id,
                PaperPosition.status == "closed",
            )
            .order_by(PaperPosition.closed_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def list_closed_since(
        self, account_id: int, since: datetime, *, limit: int = 50
    ) -> list[PaperPosition]:
        result = await self._session.execute(
            select(PaperPosition)
            .where(
                PaperPosition.account_id == account_id,
                PaperPosition.status == "closed",
                PaperPosition.closed_at.is_not(None),
                PaperPosition.closed_at >= since,
            )
            .order_by(PaperPosition.closed_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def count_opened_since(self, account_id: int, since: datetime) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(PaperPosition)
            .where(
                PaperPosition.account_id == account_id,
                PaperPosition.opened_at >= since,
                PaperPosition.status.in_(("open", "pending", "closed")),
            )
        )
        return int(result.scalar_one())

    async def add_position(self, position: PaperPosition) -> PaperPosition:
        self._session.add(position)
        await self._session.flush()
        return position

    async def add_fill(self, fill: PaperFill) -> PaperFill:
        self._session.add(fill)
        await self._session.flush()
        return fill

    async def list_fills_for_account(self, account_id: int) -> list[PaperFill]:
        """Alle Fills des Kontos chronologisch (fuer Equity-Kurve)."""
        result = await self._session.execute(
            select(PaperFill)
            .join(PaperPosition, PaperFill.position_id == PaperPosition.id)
            .where(PaperPosition.account_id == account_id)
            .order_by(PaperFill.filled_at.asc(), PaperFill.id.asc())
        )
        return list(result.scalars())

    async def update_cash(self, account_id: int, cash_balance: Decimal, realized_pnl: Decimal) -> None:
        await self._session.execute(
            update(PaperAccount)
            .where(PaperAccount.id == account_id)
            .values(cash_balance=cash_balance, realized_pnl=realized_pnl)
        )
