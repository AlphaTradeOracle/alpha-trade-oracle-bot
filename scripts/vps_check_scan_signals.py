"""Check scan cadence and recent signals on the live worker DB."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.logging import configure_logging
from app.database.session import session_scope


async def main() -> None:
    configure_logging("WARNING", json_output=False)
    async with session_scope() as session:
        print("=== NOW UTC ===")
        print((await session.execute(text("SELECT NOW() AT TIME ZONE 'UTC'"))).scalar())

        print("\n=== TABLES LIKE %job%/scan%/event% ===")
        tables = (
            await session.execute(
                text(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema='public'
                      AND (table_name ILIKE '%job%' OR table_name ILIKE '%scan%'
                           OR table_name ILIKE '%event%' OR table_name ILIKE '%sched%')
                    ORDER BY 1
                    """
                )
            )
        ).scalars().all()
        print(list(tables))

        print("\n=== SIGNALS 24h ===")
        n24 = (
            await session.execute(
                text("SELECT COUNT(*) FROM signals WHERE created_at > NOW() - INTERVAL '24 hours'")
            )
        ).scalar()
        print("signals_24h", n24)

        by_dir = (
            await session.execute(
                text(
                    """
                    SELECT direction, COUNT(*) AS n
                    FROM signals
                    WHERE created_at > NOW() - INTERVAL '24 hours'
                    GROUP BY 1 ORDER BY 2 DESC
                    """
                )
            )
        ).all()
        for d, n in by_dir:
            print(d, n)

        print("\n=== SIGNALS BY HOUR (24h) ===")
        by_hour = (
            await session.execute(
                text(
                    """
                    SELECT date_trunc('hour', created_at) AS hour_utc, COUNT(*) AS n
                    FROM signals
                    WHERE created_at > NOW() - INTERVAL '24 hours'
                    GROUP BY 1 ORDER BY 1 DESC
                    """
                )
            )
        ).all()
        for hour, n in by_hour:
            print(hour, n)

        print("\n=== LATEST 25 SIGNALS ===")
        latest = (
            await session.execute(
                text(
                    """
                    SELECT s.id, a.symbol, s.direction, ROUND(s.score::numeric, 1) AS score,
                           s.created_at
                    FROM signals s
                    JOIN assets a ON a.id = s.asset_id
                    ORDER BY s.created_at DESC
                    LIMIT 25
                    """
                )
            )
        ).all()
        for row in latest:
            print(tuple(row))

        print("\n=== LAST SIGNAL AGE ===")
        age = (
            await session.execute(
                text(
                    """
                    SELECT MAX(created_at) AS last_at,
                           EXTRACT(EPOCH FROM (NOW() - MAX(created_at)))/60.0 AS minutes_ago
                    FROM signals
                    """
                )
            )
        ).one()
        print("last_at", age[0], "minutes_ago", round(float(age[1] or 0), 1))

        print("\n=== ACTIONABLE LAST 24h ===")
        actionable = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM signals
                    WHERE created_at > NOW() - INTERVAL '24 hours'
                      AND direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')
                    """
                )
            )
        ).scalar()
        print("actionable_24h", actionable)

        print("\n=== DELIVERIES 24h ===")
        try:
            dels = (
                await session.execute(
                    text(
                        """
                        SELECT status, COUNT(*)
                        FROM signal_deliveries
                        WHERE created_at > NOW() - INTERVAL '24 hours'
                        GROUP BY 1 ORDER BY 2 DESC
                        """
                    )
                )
            ).all()
            print(dels or "(none)")
        except Exception as exc:
            print("deliveries_error", exc)

        print("\n=== EVENTS recent scan-related ===")
        if "events" in tables:
            ev = (
                await session.execute(
                    text(
                        """
                        SELECT created_at, event_type, severity, LEFT(message, 160)
                        FROM events
                        WHERE created_at > NOW() - INTERVAL '6 hours'
                          AND (event_type ILIKE '%scan%' OR message ILIKE '%scan%'
                               OR event_type ILIKE '%universe%' OR message ILIKE '%universe%')
                        ORDER BY created_at DESC
                        LIMIT 30
                        """
                    )
                )
            ).all()
            for row in ev:
                print(tuple(row))
            if not ev:
                print("(no scan events in 6h)")

        print("\n=== UNIVERSE SIZE ===")
        uni = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FILTER (WHERE in_universe), COUNT(*) FROM assets"
                )
            )
        ).one()
        print("in_universe", uni[0], "total_assets", uni[1])

        print("\n=== PAPER OPENS LAST 24h ===")
        try:
            paper = (
                await session.execute(
                    text(
                        """
                        SELECT COUNT(*) FILTER (WHERE status='open') AS open_n,
                               COUNT(*) FILTER (WHERE status='pending') AS pending_n,
                               COUNT(*) FILTER (
                                 WHERE opened_at > NOW() - INTERVAL '24 hours'
                                   AND status IN ('open','closed','pending')
                               ) AS opened_24h
                        FROM paper_positions
                        """
                    )
                )
            ).one()
            print(dict(paper._mapping))
        except Exception as exc:
            print(exc)


if __name__ == "__main__":
    asyncio.run(main())
