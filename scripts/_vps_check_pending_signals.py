import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import session_scope
from app.models.paper import PaperAccount, PaperPosition
from app.strategies.weights import DEFAULT_WEIGHTS


async def main() -> None:
    configure_logging("ERROR", json_output=False)
    s = get_settings()
    print("=== gates ===")
    print(
        {
            "short_max": s.signal_short_max_score,
            "short_min": s.signal_short_min_score,
            "long_min": s.signal_min_score,
            "retest": s.paper_retest_entry_enabled,
            "cooldown_m": s.signal_cooldown_minutes,
            "mtf": DEFAULT_WEIGHTS.multi_timeframe,
            "structure": DEFAULT_WEIGHTS.market_structure,
            "paper_enabled": s.enable_paper_trading,
        }
    )

    async with session_scope() as session:
        acct = (
            await session.execute(select(PaperAccount).where(PaperAccount.name == "default"))
        ).scalar_one()
        print(
            "account",
            {
                "cash": float(acct.cash_balance),
                "realized": float(acct.realized_pnl),
                "initial": float(acct.initial_balance),
            },
        )

        rows = (
            await session.execute(
                select(PaperPosition)
                .where(PaperPosition.account_id == acct.id)
                .order_by(PaperPosition.opened_at.desc())
            )
        ).scalars().all()
        by = Counter(p.status for p in rows)
        print("positions_by_status", dict(by))

        pending = [p for p in rows if p.status == "pending"]
        print("pending_n", len(pending))
        for p in pending[:30]:
            print(
                {
                    "symbol": p.symbol,
                    "dir": p.direction,
                    "score": float(p.signal_score or 0),
                    "entry": float(p.entry_price or 0),
                    "opened_at": str(p.opened_at),
                    "expires_at": str(p.expires_at),
                    "notes": (p.notes or "")[:120],
                }
            )

        open_ = [p for p in rows if p.status == "open"]
        print("open_n", len(open_))
        for p in open_[:10]:
            print(
                {
                    "symbol": p.symbol,
                    "dir": p.direction,
                    "score": float(p.signal_score or 0),
                    "opened_at": str(p.opened_at),
                }
            )

        since = datetime.now(timezone.utc) - timedelta(hours=6)
        sig = (
            await session.execute(
                text(
                    """
                    SELECT direction, COUNT(*) n,
                           COUNT(*) FILTER (WHERE is_dispatched) AS dispatched,
                           MAX(created_at) AS last_at
                    FROM signals
                    WHERE created_at >= :since
                      AND direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')
                    GROUP BY 1
                    ORDER BY 1
                    """
                ),
                {"since": since},
            )
        ).mappings().all()
        print("signals_last_6h", [dict(r) for r in sig])

        recent = (
            await session.execute(
                text(
                    """
                    SELECT s.created_at, a.symbol, s.direction, s.score, s.is_dispatched,
                           s.data_quality, s.risk_reward_ratio
                    FROM signals s
                    JOIN assets a ON a.id = s.asset_id
                    WHERE s.created_at >= :since
                      AND s.direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')
                    ORDER BY s.created_at DESC
                    LIMIT 25
                    """
                ),
                {"since": since},
            )
        ).mappings().all()
        print("recent_actionable")
        for r in recent:
            print(dict(r))

        # score band check vs gates
        band = (
            await session.execute(
                text(
                    """
                    SELECT
                      COUNT(*) FILTER (
                        WHERE direction IN ('SHORT','STRONG_SHORT')
                          AND score > :smin AND score <= :smax
                      ) AS short_in_band,
                      COUNT(*) FILTER (
                        WHERE direction IN ('LONG','STRONG_LONG') AND score >= :lmin
                      ) AS long_in_band,
                      COUNT(*) FILTER (
                        WHERE direction IN ('SHORT','STRONG_SHORT') AND score > :smax
                      ) AS short_above_max,
                      COUNT(*) FILTER (
                        WHERE direction IN ('SHORT','STRONG_SHORT') AND score <= :smin
                      ) AS short_exhaust
                    FROM signals
                    WHERE created_at >= :since
                      AND direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')
                    """
                ),
                {
                    "since": since,
                    "smin": float(s.signal_short_min_score),
                    "smax": float(s.signal_short_max_score),
                    "lmin": float(s.signal_min_score),
                },
            )
        ).mappings().one()
        print("band_last_6h", dict(band))


asyncio.run(main())
