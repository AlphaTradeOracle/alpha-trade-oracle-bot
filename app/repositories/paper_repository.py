"""Repository fuer Paper-Trading-Konten und Positionen."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.paper import PaperAccount, PaperFill, PaperPosition


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

    async def get_open_by_symbol(self, account_id: int, symbol: str) -> PaperPosition | None:
        result = await self._session.execute(
            select(PaperPosition).where(
                PaperPosition.account_id == account_id,
                PaperPosition.symbol == symbol.upper(),
                PaperPosition.status == "open",
            )
        )
        return result.scalar_one_or_none()

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

    async def add_position(self, position: PaperPosition) -> PaperPosition:
        self._session.add(position)
        await self._session.flush()
        return position

    async def add_fill(self, fill: PaperFill) -> PaperFill:
        self._session.add(fill)
        await self._session.flush()
        return fill

    async def update_cash(self, account_id: int, cash_balance: Decimal, realized_pnl: Decimal) -> None:
        await self._session.execute(
            update(PaperAccount)
            .where(PaperAccount.id == account_id)
            .values(cash_balance=cash_balance, realized_pnl=realized_pnl)
        )
