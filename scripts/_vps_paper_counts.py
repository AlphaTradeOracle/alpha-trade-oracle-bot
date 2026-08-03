"""Quick paper position counts."""

import asyncio

from sqlalchemy import text

from app.core.logging import configure_logging
from app.database.session import session_scope


async def main() -> None:
    configure_logging("WARNING", json_output=False)
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT status, COUNT(*) AS n,
                           ROUND(COALESCE(SUM(realized_pnl), 0)::numeric, 2) AS pnl
                    FROM paper_positions
                    GROUP BY 1
                    ORDER BY 1
                    """
                )
            )
        ).all()
        print("STATUS")
        for r in rows:
            print(tuple(r))

        rows = (
            await session.execute(
                text(
                    """
                    SELECT exit_reason, COUNT(*) AS n,
                           ROUND(COALESCE(SUM(realized_pnl), 0)::numeric, 2) AS pnl
                    FROM paper_positions
                    WHERE status = 'closed'
                    GROUP BY 1
                    ORDER BY 2 DESC
                    """
                )
            )
        ).all()
        print("EXITS")
        for r in rows:
            print(tuple(r))


if __name__ == "__main__":
    asyncio.run(main())
