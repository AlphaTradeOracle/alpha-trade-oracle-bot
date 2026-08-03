"""Quick VPS status: recent signals + near misses."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, func, select, text

from app.database.session import get_session_factory
from app.models.asset import Asset
from app.models.signal import Signal


async def main() -> None:
    sf = get_session_factory()
    async with sf() as s:
        now = datetime.now(timezone.utc)
        print("UTC", now.isoformat())

        for label, delta in (
            ("6h", timedelta(hours=6)),
            ("24h", timedelta(hours=24)),
            ("7d", timedelta(days=7)),
            ("30d", timedelta(days=30)),
        ):
            since = now - delta
            total = (
                await s.execute(
                    select(func.count()).select_from(Signal).where(Signal.created_at >= since)
                )
            ).scalar()
            disp = (
                await s.execute(
                    select(func.count())
                    .select_from(Signal)
                    .where(Signal.created_at >= since, Signal.is_dispatched.is_(True))
                )
            ).scalar()
            print(f"count_{label}: total={total} dispatched={disp}")

        print("\n=== LAST 25 SIGNALS ===")
        q = await s.execute(
            select(Signal, Asset.symbol)
            .join(Asset, Asset.id == Signal.asset_id)
            .order_by(desc(Signal.created_at))
            .limit(25)
        )
        for sig, symbol in q.all():
            print(
                f"{sig.created_at.isoformat()} {symbol:14} {sig.direction:5} "
                f"score={float(sig.score):5.1f} conf={sig.confidence:8} "
                f"phase={sig.market_phase:12} disp={sig.is_dispatched} "
                f"rr={sig.risk_reward_ratio}"
            )

        print("\n=== SCORE BUCKETS 7d ===")
        dist = await s.execute(
            text(
                """
                SELECT
                  CASE
                    WHEN score >= 75 THEN 'a_75+'
                    WHEN score >= 70 THEN 'b_70-74'
                    WHEN score >= 65 THEN 'c_65-69'
                    WHEN score >= 60 THEN 'd_60-64'
                    ELSE 'e_<60'
                  END AS bucket,
                  COUNT(*) AS n,
                  ROUND(AVG(score)::numeric, 1) AS avg_score,
                  SUM(CASE WHEN is_dispatched THEN 1 ELSE 0 END) AS dispatched
                FROM signals
                WHERE created_at >= NOW() - INTERVAL '7 days'
                GROUP BY 1
                ORDER BY 1
                """
            )
        )
        for row in dist:
            print(dict(row._mapping))

        print("\n=== NEAR MISSES 48h (score 65-74.99) ===")
        near = await s.execute(
            text(
                """
                SELECT s.created_at, a.symbol, s.direction, s.score, s.confidence,
                       s.market_phase, s.is_dispatched, s.risk_reward_ratio
                FROM signals s
                JOIN assets a ON a.id = s.asset_id
                WHERE s.created_at >= NOW() - INTERVAL '48 hours'
                  AND s.score >= 65 AND s.score < 75
                ORDER BY s.score DESC, s.created_at DESC
                LIMIT 30
                """
            )
        )
        rows = list(near)
        print(f"count_shown={len(rows)}")
        for row in rows:
            m = row._mapping
            print(
                f"{m['created_at']} {m['symbol']:14} {m['direction']:5} "
                f"score={float(m['score']):5.1f} conf={m['confidence']} "
                f"phase={m['market_phase']} disp={m['is_dispatched']} rr={m['risk_reward_ratio']}"
            )

        print("\n=== TOP scores 48h ===")
        top = await s.execute(
            text(
                """
                SELECT s.created_at, a.symbol, s.direction, s.score, s.confidence,
                       s.is_dispatched, s.market_phase
                FROM signals s
                JOIN assets a ON a.id = s.asset_id
                WHERE s.created_at >= NOW() - INTERVAL '48 hours'
                ORDER BY s.score DESC
                LIMIT 20
                """
            )
        )
        for row in top:
            m = row._mapping
            print(
                f"{m['created_at']} {m['symbol']:14} {m['direction']:5} "
                f"score={float(m['score']):5.1f} conf={m['confidence']} "
                f"phase={m['market_phase']} disp={m['is_dispatched']}"
            )

        print("\n=== DIRECTION / CONF 7d ===")
        mix = await s.execute(
            text(
                """
                SELECT direction, confidence, COUNT(*) AS n,
                       SUM(CASE WHEN is_dispatched THEN 1 ELSE 0 END) AS dispatched,
                       ROUND(MAX(score)::numeric, 1) AS max_score
                FROM signals
                WHERE created_at >= NOW() - INTERVAL '7 days'
                GROUP BY 1, 2
                ORDER BY n DESC
                """
            )
        )
        for row in mix:
            print(dict(row._mapping))

        print("\n=== JOB RUNS (if any) ===")
        try:
            jobs = await s.execute(
                text(
                    """
                    SELECT * FROM job_runs
                    ORDER BY 1 DESC
                    LIMIT 5
                    """
                )
            )
            print("sample", [dict(r._mapping) for r in jobs])
        except Exception as exc:
            print("job_runs:", type(exc).__name__, str(exc)[:160])


if __name__ == "__main__":
    asyncio.run(main())
