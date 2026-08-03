"""Why actionable signals are not dispatched / papered."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.logging import configure_logging
from app.database.session import session_scope


async def main() -> None:
    configure_logging("WARNING", json_output=False)
    async with session_scope() as session:
        print("=== SCHEDULED JOBS ===")
        rows = (
            await session.execute(
                text(
                    """
                    SELECT job_key, interval_seconds, last_run_at, last_success_at,
                           next_run_at, last_status, LEFT(COALESCE(last_error,''),120),
                           run_count, is_enabled
                    FROM scheduled_jobs
                    ORDER BY job_key
                    """
                )
            )
        ).all()
        for r in rows:
            print(tuple(r))

        print("\n=== ACTIONABLE LAST 6h (top) ===")
        acts = (
            await session.execute(
                text(
                    """
                    SELECT a.symbol, s.direction, ROUND(s.score::numeric,1), s.created_at
                    FROM signals s
                    JOIN assets a ON a.id = s.asset_id
                    WHERE s.created_at > NOW() - INTERVAL '6 hours'
                      AND s.direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')
                    ORDER BY s.created_at DESC
                    LIMIT 30
                    """
                )
            )
        ).all()
        for r in acts:
            print(tuple(r))
        if not acts:
            print("(none in 6h)")

        print("\n=== SUPPRESSION REASONS 6h ===")
        # deliveries may store reason in status or payload
        cols = (
            await session.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name='signal_deliveries' ORDER BY 1
                    """
                )
            )
        ).scalars().all()
        print("delivery_cols", list(cols))

        if "suppression_reason" in cols or "reason" in cols:
            reason_col = "suppression_reason" if "suppression_reason" in cols else "reason"
            reasons = (
                await session.execute(
                    text(
                        f"""
                        SELECT COALESCE({reason_col}::text, status), COUNT(*)
                        FROM signal_deliveries
                        WHERE created_at > NOW() - INTERVAL '6 hours'
                        GROUP BY 1 ORDER BY 2 DESC
                        LIMIT 20
                        """
                    )
                )
            ).all()
            for r in reasons:
                print(tuple(r))

        print("\n=== DISPATCHED ANY TIME LAST 48h ===")
        disp = (
            await session.execute(
                text(
                    """
                    SELECT status, COUNT(*)
                    FROM signal_deliveries
                    WHERE created_at > NOW() - INTERVAL '48 hours'
                    GROUP BY 1
                    """
                )
            )
        ).all()
        print(disp)

        print("\n=== LAST ACTIONABLE AGE ===")
        last = (
            await session.execute(
                text(
                    """
                    SELECT MAX(created_at),
                           EXTRACT(EPOCH FROM (NOW()-MAX(created_at)))/60.0
                    FROM signals
                    WHERE direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')
                    """
                )
            )
        ).one()
        print("last_actionable", last[0], "minutes_ago", round(float(last[1] or 0), 1))

        print("\n=== ACTIONABLE BY HOUR 24h ===")
        by_h = (
            await session.execute(
                text(
                    """
                    SELECT date_trunc('hour', created_at), direction, COUNT(*)
                    FROM signals
                    WHERE created_at > NOW() - INTERVAL '24 hours'
                      AND direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')
                    GROUP BY 1, 2
                    ORDER BY 1 DESC, 3 DESC
                    LIMIT 40
                    """
                )
            )
        ).all()
        for r in by_h:
            print(tuple(r))


if __name__ == "__main__":
    asyncio.run(main())
