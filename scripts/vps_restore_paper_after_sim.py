"""Remove sim pollution from default paper account; keep the 14 closed same-entry trades."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from sqlalchemy import text

from app.database.session import session_scope


async def main() -> None:
    async with session_scope() as s:
        r = await s.execute(
            text(
                """
                delete from paper_fills where position_id in (
                  select id from paper_positions
                  where account_id=1 and status in ('open','pending','cancelled')
                )
                """
            )
        )
        print("deleted_fills", r.rowcount)
        r = await s.execute(
            text(
                """
                delete from paper_positions
                where account_id=1 and status in ('open','pending','cancelled')
                """
            )
        )
        print("deleted_positions", r.rowcount)

        r = await s.execute(
            text(
                """
                delete from paper_fills where position_id in (
                  select id from paper_positions
                  where account_id in (
                    select id from paper_accounts where name like 'sim_gate_%'
                  )
                )
                """
            )
        )
        print("sim_fills", r.rowcount)
        r = await s.execute(
            text(
                """
                delete from paper_positions where account_id in (
                  select id from paper_accounts where name like 'sim_gate_%'
                )
                """
            )
        )
        print("sim_pos", r.rowcount)
        r = await s.execute(text("delete from paper_accounts where name like 'sim_gate_%'"))
        print("sim_accts", r.rowcount)

        row = (
            await s.execute(
                text(
                    """
                    select coalesce(sum(realized_pnl),0) as rp from paper_positions
                    where account_id=1 and status='closed'
                    """
                )
            )
        ).mappings().one()
        rp = Decimal(str(row["rp"]))
        await s.execute(
            text(
                """
                update paper_accounts
                set cash_balance = initial_balance + :rp, realized_pnl = :rp
                where id=1
                """
            ),
            {"rp": rp},
        )
        check = (
            await s.execute(
                text(
                    """
                    select name, cash_balance, realized_pnl,
                      (select count(*) from paper_positions p
                       where p.account_id=a.id and p.status='open') as open_n,
                      (select count(*) from paper_positions p
                       where p.account_id=a.id and p.status='closed') as closed_n,
                      (select count(*) from paper_positions p
                       where p.account_id=a.id) as total_n
                    from paper_accounts a where a.id=1
                    """
                )
            )
        ).mappings().one()
        print("RESTORED", dict(check))


if __name__ == "__main__":
    asyncio.run(main())
