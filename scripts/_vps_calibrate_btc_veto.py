"""Calibrate BTC 1h/4h returns at SHORT paper entry times."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.core.logging import configure_logging
from app.core.time import ensure_utc
from app.database.session import session_scope

SINCE = datetime(2026, 7, 31, 16, 32, 35, tzinfo=timezone.utc)


def ret(df: pd.DataFrame, asof, hours: int) -> float | None:
    asof = ensure_utc(asof)
    dur = pd.Timedelta(hours=hours)
    hist = df.loc[(df.index + dur) <= pd.Timestamp(asof)]
    if len(hist) < 1:
        return None
    o = float(hist.iloc[-1]["open"])
    c = float(hist.iloc[-1]["close"])
    return (c / o - 1) * 100 if o > 0 else None


async def main() -> None:
    configure_logging("ERROR", json_output=False)
    async with session_scope() as s:
        trades = (
            await s.execute(
                text(
                    """
                    SELECT p.opened_at, p.realized_pnl::float AS pnl, p.symbol
                    FROM paper_positions p
                    JOIN paper_accounts a ON a.id = p.account_id
                    WHERE a.name = 'default' AND p.status = 'closed'
                      AND p.direction = 'SHORT' AND p.opened_at >= :s
                    ORDER BY p.opened_at
                    """
                ),
                {"s": SINCE},
            )
        ).mappings().all()
        frames: dict[str, pd.DataFrame] = {}
        for tf in ("1h", "4h"):
            rows = (
                await s.execute(
                    text(
                        """
                        SELECT c.open_time, c.open, c.close
                        FROM market_candles c
                        JOIN assets a ON a.id = c.asset_id
                        WHERE a.symbol = 'BTCUSDT' AND c.timeframe = :tf
                          AND c.open_time >= '2026-07-20'
                          AND c.open_time <= '2026-08-05'
                        ORDER BY c.open_time
                        """
                    ),
                    {"tf": tf},
                )
            ).mappings().all()
            idx = pd.to_datetime([r["open_time"] for r in rows], utc=True)
            frames[tf] = pd.DataFrame(
                {
                    "open": [float(r["open"]) for r in rows],
                    "close": [float(r["close"]) for r in rows],
                },
                index=idx,
            )

    r1s: list[float] = []
    r4s: list[float] = []
    both_green = either_green = 0
    buckets: Counter = Counter()
    thr_list = [
        (0.0, 0.0),
        (0.1, 0.2),
        (0.15, 0.3),
        (0.2, 0.4),
        (0.25, 0.5),
        (0.3, 0.6),
        (0.5, 1.0),
    ]
    outcomes = {thr: {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0} for thr in thr_list}

    for t in trades:
        r1 = ret(frames["1h"], t["opened_at"], 1)
        r4 = ret(frames["4h"], t["opened_at"], 4)
        if r1 is None or r4 is None:
            continue
        r1s.append(r1)
        r4s.append(r4)
        if r1 > 0 and r4 > 0:
            both_green += 1
        if r1 > 0 or r4 > 0:
            either_green += 1
        for thr in thr_list:
            if r1 >= thr[0] and r4 >= thr[1]:
                buckets[thr] += 1
                o = outcomes[thr]
                o["n"] += 1
                o["pnl"] += float(t["pnl"])
                if float(t["pnl"]) > 0:
                    o["wins"] += 1
                elif float(t["pnl"]) < 0:
                    o["losses"] += 1

    print("n", len(r1s), "both_green", both_green, "either_green", either_green)
    print(
        "r1_pct",
        {k: round(float(np.percentile(r1s, k)), 3) for k in (10, 25, 50, 75, 90, 95, 99)},
    )
    print(
        "r4_pct",
        {k: round(float(np.percentile(r4s, k)), 3) for k in (10, 25, 50, 75, 90, 95, 99)},
    )
    for thr, o in outcomes.items():
        kept_n = len(r1s) - o["n"]
        # Approximate equity if removed: total pnl - removed pnl
        print(
            "thr",
            thr,
            "removed",
            o["n"],
            "removed_pnl",
            round(o["pnl"], 2),
            "removed_WR",
            round(o["wins"] / o["n"] * 100, 1) if o["n"] else None,
        )


if __name__ == "__main__":
    asyncio.run(main())
