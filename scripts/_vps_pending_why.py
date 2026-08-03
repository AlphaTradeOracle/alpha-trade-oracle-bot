"""Why no pending trades + signal alignment check."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import session_scope
from app.repositories.strategy_repository import DEFAULT_STRATEGY_NAME, StrategyRepository
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights


async def main() -> None:
    configure_logging("ERROR", json_output=False)
    cfg = get_settings()
    print("utc_now", datetime.now(timezone.utc).isoformat())
    print(
        "gates",
        {
            "short": (cfg.signal_short_min_score, cfg.signal_short_max_score),
            "long_min": cfg.signal_min_score,
            "retest": cfg.paper_retest_entry_enabled,
            "cooldown_m": cfg.signal_cooldown_minutes,
            "paper": cfg.enable_paper_trading,
            "universe_scan": cfg.enable_universe_scan,
        },
    )

    async with session_scope() as s:
        active = await StrategyRepository(s).get_active_version(DEFAULT_STRATEGY_NAME)
        aw = StrategyWeights.from_db_columns(active)
        print("weights_active_v", active.version)
        print("weights_match_default", aw.model_dump() == DEFAULT_WEIGHTS.model_dump())
        print("weights", aw.model_dump())

        acct = (
            await s.execute(
                text(
                    "SELECT id, name, cash_balance, realized_pnl "
                    "FROM paper_accounts WHERE name='default'"
                )
            )
        ).mappings().one()
        print("account", dict(acct))
        aid = int(acct["id"])

        rows = (
            await s.execute(
                text(
                    "SELECT status, COUNT(*)::int AS n FROM paper_positions "
                    "WHERE account_id=:aid GROUP BY 1 ORDER BY 1"
                ),
                {"aid": aid},
            )
        ).mappings().all()
        print("paper_by_status", [dict(r) for r in rows])

        pending = (
            await s.execute(
                text(
                    "SELECT id, symbol, direction, status, signal_score, opened_at, "
                    "entry_price, expires_at FROM paper_positions "
                    "WHERE account_id=:aid AND status IN ('pending','open') "
                    "ORDER BY opened_at DESC"
                ),
                {"aid": aid},
            )
        ).mappings().all()
        print("open_or_pending", [dict(r) for r in pending])

        print("--- cancelled exit reasons ---")
        for r in (
            await s.execute(
                text(
                    "SELECT exit_reason, COUNT(*)::int AS n FROM paper_positions "
                    "WHERE account_id=:aid AND status='cancelled' "
                    "GROUP BY 1 ORDER BY n DESC LIMIT 15"
                ),
                {"aid": aid},
            )
        ).mappings().all():
            print(dict(r))

        print("--- SKR paper ---")
        for r in (
            await s.execute(
                text(
                    "SELECT id, symbol, direction, status, signal_score, opened_at, "
                    "closed_at, exit_reason FROM paper_positions "
                    "WHERE account_id=:aid AND symbol='SKRUSDT' "
                    "ORDER BY opened_at DESC LIMIT 10"
                ),
                {"aid": aid},
            )
        ).mappings().all():
            print(dict(r))

        print("--- actionable signals 12h ---")
        for r in (
            await s.execute(
                text(
                    """
                    SELECT s.created_at, ast.symbol, s.direction, s.score, s.is_dispatched
                    FROM signals s
                    JOIN assets ast ON ast.id = s.asset_id
                    WHERE s.created_at > NOW() - INTERVAL '12 hours'
                      AND (
                        (s.direction = 'SHORT' AND s.score BETWEEN :smin AND :smax)
                        OR (s.direction = 'LONG' AND s.score >= :lmin)
                      )
                    ORDER BY s.created_at DESC
                    LIMIT 30
                    """
                ),
                {
                    "smin": float(cfg.signal_short_min_score),
                    "smax": float(cfg.signal_short_max_score),
                    "lmin": float(cfg.signal_min_score),
                },
            )
        ).mappings().all():
            print(dict(r))

        print("--- last 5 closed ---")
        for r in (
            await s.execute(
                text(
                    "SELECT symbol, direction, signal_score, opened_at, closed_at, "
                    "exit_reason, realized_pnl FROM paper_positions "
                    "WHERE account_id=:aid AND status='closed' "
                    "ORDER BY closed_at DESC NULLS LAST LIMIT 5"
                ),
                {"aid": aid},
            )
        ).mappings().all():
            print(dict(r))


if __name__ == "__main__":
    asyncio.run(main())
