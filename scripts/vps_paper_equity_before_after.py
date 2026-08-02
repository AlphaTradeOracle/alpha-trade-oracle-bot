"""Print current paper equity, optionally rebuild with perp prices, print after."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from sqlalchemy import select, func

from app.container import build_container
from app.core.logging import configure_logging
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.models.signal import Signal


async def dump(label: str) -> None:
    container = build_container()
    try:
        async with session_scope() as session:
            summary = await container.paper_trading.summary(session)
        ret = 0.0
        if summary.initial_balance:
            ret = (summary.equity / summary.initial_balance - 1.0) * 100.0
        print(f"=== {label} ===")
        print(f"equity={summary.equity:.2f}")
        print(f"cash={summary.cash_balance:.2f}")
        print(f"realized_pnl={summary.realized_pnl:.2f}")
        print(f"open_positions={summary.open_positions}")
        print(f"pending_positions={summary.pending_positions}")
        print(f"closed_trades={summary.closed_trades}")
        print(f"win_rate={summary.win_rate:.1f}")
        print(f"total_return_pct={ret:.2f}")
        print(f"start_capital={summary.initial_balance:.2f}")
    finally:
        await container.aclose()


async def rebuild(since: str) -> None:
    container = build_container()
    try:
        if since.strip().lower() == "today":
            now = utc_now()
            since_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            since_dt = ensure_utc(datetime.fromisoformat(since))

        async with session_scope() as session:
            # earliest signal hint
            earliest = (
                await session.execute(select(func.min(Signal.created_at)))
            ).scalar()
            print(f"rebuild_since={since_dt.isoformat()} earliest_signal={earliest}")

            result = await container.paper_trading.rebuild_from_signals(
                session,
                since=since_dt,
                provider=container.paper_price_provider,
                providers=None,
                dispatched_only=False,
                one_per_symbol=False,
                symbols=None,
            )
            summary = await container.paper_trading.summary(session)
            opened = result.backfill.opened if result.backfill else 0
            print("=== REBUILD DONE ===")
            print(f"reset_positions={result.reset_positions}")
            print(f"opened={opened}")
            print(f"retest_filled={result.retest_filled}")
            print(f"retest_skipped={result.retest_skipped}")
            print(f"replayed={result.replayed}")
            print(f"still_open={result.still_open}")
            ret = 0.0
            if summary.initial_balance:
                ret = (summary.equity / summary.initial_balance - 1.0) * 100.0
            print(f"equity={summary.equity:.2f}")
            print(f"cash={summary.cash_balance:.2f}")
            print(f"realized_pnl={summary.realized_pnl:.2f}")
            print(f"open_positions={summary.open_positions}")
            print(f"closed_trades={summary.closed_trades}")
            print(f"total_return_pct={ret:.2f}")
    finally:
        await container.aclose()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--since", default="2026-07-01T00:00:00+00:00")
    args = parser.parse_args()
    configure_logging("INFO", json_output=False)
    await dump("BEFORE")
    if args.rebuild:
        await rebuild(args.since)
        await dump("AFTER")


if __name__ == "__main__":
    asyncio.run(main())
