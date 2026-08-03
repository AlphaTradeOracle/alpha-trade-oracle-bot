"""DB/config readiness bits for 24/7."""
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
    print("utc", datetime.now(timezone.utc).isoformat())
    print(
        "runtime",
        {
            "paper": cfg.enable_paper_trading,
            "universe_scan": cfg.enable_universe_scan,
            "telegram_dispatch": cfg.telegram_signal_dispatch,
            "short": (cfg.signal_short_min_score, cfg.signal_short_max_score),
            "long_min": cfg.signal_min_score,
            "retest": cfg.paper_retest_entry_enabled,
            "cooldown_m": cfg.signal_cooldown_minutes,
            "regime": cfg.market_regime_enabled,
            "hard_veto": cfg.market_regime_hard_veto,
            "max_open": cfg.paper_max_open_positions,
            "max_per_dir": cfg.paper_max_open_per_direction,
            "universe_batch": cfg.universe_scan_batch_size,
        },
    )
    async with session_scope() as s:
        active = await StrategyRepository(s).get_active_version(DEFAULT_STRATEGY_NAME)
        w = StrategyWeights.from_db_columns(active)
        print("strategy", active.version, "match_default", w.model_dump() == DEFAULT_WEIGHTS.model_dump())
        print("weights_mtf_structure", w.multi_timeframe, w.market_structure)

        uni = (
            await s.execute(
                text(
                    """
                    SELECT COUNT(*) FILTER (WHERE in_universe AND is_active) AS in_uni,
                           COUNT(*) FILTER (WHERE in_universe AND is_active AND market_cap_rank IS NOT NULL) AS ranked
                    FROM assets
                    """
                )
            )
        ).mappings().one()
        print("universe", dict(uni))

        sig = (
            await s.execute(
                text(
                    """
                    SELECT MAX(created_at) AS last_signal,
                           COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '6 hours') AS n_6h,
                           COUNT(*) FILTER (
                             WHERE created_at > NOW() - INTERVAL '6 hours'
                               AND (
                                 (direction='SHORT' AND score BETWEEN :smin AND :smax)
                                 OR (direction IN ('LONG','STRONG_LONG') AND score >= :lmin)
                               )
                           ) AS actionable_6h
                    FROM signals
                    """
                ),
                {
                    "smin": float(cfg.signal_short_min_score),
                    "smax": float(cfg.signal_short_max_score),
                    "lmin": float(cfg.signal_min_score),
                },
            )
        ).mappings().one()
        print("signals", dict(sig))

        jobs = (
            await s.execute(
                text(
                    """
                    SELECT job_key, status, last_started_at, last_finished_at, last_error
                    FROM scheduler_jobs
                    ORDER BY job_key
                    """
                )
            )
        ).mappings().all()
        print("jobs")
        for j in jobs:
            print(" ", dict(j))


if __name__ == "__main__":
    asyncio.run(main())
