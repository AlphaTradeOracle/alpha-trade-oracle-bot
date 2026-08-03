import asyncio
from sqlalchemy import text
from app.core.logging import configure_logging
from app.database.session import session_scope

configure_logging("ERROR", json_output=False)


async def main() -> None:
    async with session_scope() as session:
        tables = (
            await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name ILIKE '%paper%' "
                    "ORDER BY 1"
                )
            )
        ).scalars().all()
        print("tables", tables)
        for t in tables:
            cols = (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name=:t ORDER BY ordinal_position"
                    ),
                    {"t": t},
                )
            ).scalars().all()
            n = (await session.execute(text(f"SELECT COUNT(*) FROM {t}"))).scalar()
            print(t, "count=", n, "cols=", cols)

        # common account fields
        for sql in (
            "SELECT * FROM paper_accounts LIMIT 5",
            "SELECT account_id, cash, equity, realized_pnl, updated_at FROM paper_accounts LIMIT 5",
        ):
            try:
                rows = (await session.execute(text(sql))).mappings().all()
                print("OK", sql[:60], [dict(r) for r in rows])
            except Exception as exc:
                print("FAIL", sql[:60], type(exc).__name__, exc)


asyncio.run(main())
