"""Richer BTC veto counterfactuals on desk SHORT fills."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import text

from app.core.logging import configure_logging
from app.core.time import ensure_utc
from app.database.session import session_scope

SINCE = datetime(2026, 7, 31, 16, 32, 35, tzinfo=timezone.utc)


def completed(df: pd.DataFrame, asof, hours: int) -> pd.DataFrame:
    asof = ensure_utc(asof)
    dur = pd.Timedelta(hours=hours)
    return df.loc[(df.index + dur) <= pd.Timestamp(asof)]


def last_ret(df: pd.DataFrame, asof, hours: int) -> float | None:
    hist = completed(df, asof, hours)
    if len(hist) < 1:
        return None
    o = float(hist.iloc[-1]["open"])
    c = float(hist.iloc[-1]["close"])
    return (c / o - 1) * 100 if o > 0 else None


def cum_ret_n(df: pd.DataFrame, asof, hours: int, n: int) -> float | None:
    hist = completed(df, asof, hours)
    if len(hist) < n:
        return None
    chunk = hist.iloc[-n:]
    o = float(chunk.iloc[0]["open"])
    c = float(chunk.iloc[-1]["close"])
    return (c / o - 1) * 100 if o > 0 else None


async def main() -> None:
    configure_logging("ERROR", json_output=False)
    async with session_scope() as s:
        trades = (
            await s.execute(
                text(
                    """
                    SELECT p.opened_at, p.realized_pnl::float AS pnl, p.symbol,
                           p.exit_reason
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
        for tf, h in (("1h", 1), ("4h", 4)):
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

    total_pnl = sum(float(t["pnl"]) for t in trades)
    baseline_eq = 5000 + total_pnl
    wins0 = sum(1 for t in trades if float(t["pnl"]) > 0)

    variants = {
        "both_green": lambda t: (
            (r1 := last_ret(frames["1h"], t["opened_at"], 1)) is not None
            and (r4 := last_ret(frames["4h"], t["opened_at"], 4)) is not None
            and r1 > 0
            and r4 > 0
        ),
        "1h_green": lambda t: ((r1 := last_ret(frames["1h"], t["opened_at"], 1)) or 0) > 0,
        "4h_green": lambda t: ((r4 := last_ret(frames["4h"], t["opened_at"], 4)) or 0) > 0,
        "1h_ge_0.25": lambda t: ((r1 := last_ret(frames["1h"], t["opened_at"], 1)) or -9) >= 0.25,
        "1h_ge_0.5": lambda t: ((r1 := last_ret(frames["1h"], t["opened_at"], 1)) or -9) >= 0.5,
        "cum_3h_ge_0.4": lambda t: ((r := cum_ret_n(frames["1h"], t["opened_at"], 1, 3)) or -9) >= 0.4,
        "cum_3h_ge_0.6_and_4h_green": lambda t: (
            ((r := cum_ret_n(frames["1h"], t["opened_at"], 1, 3)) or -9) >= 0.6
            and ((r4 := last_ret(frames["4h"], t["opened_at"], 4)) or -9) > 0
        ),
        "1h_green_and_4h_green": lambda t: (
            ((r1 := last_ret(frames["1h"], t["opened_at"], 1)) or -9) > 0
            and ((r4 := last_ret(frames["4h"], t["opened_at"], 4)) or -9) > 0
        ),
    }

    out = {
        "baseline": {
            "closed": len(trades),
            "wr": round(wins0 / len(trades) * 100, 1),
            "realized": round(total_pnl, 2),
            "equity": round(baseline_eq, 2),
        },
        "variants": {},
    }

    for name, pred in variants.items():
        removed = [t for t in trades if pred(t)]
        kept = [t for t in trades if not pred(t)]
        rp = sum(float(t["pnl"]) for t in removed)
        kp = sum(float(t["pnl"]) for t in kept)
        kw = sum(1 for t in kept if float(t["pnl"]) > 0)
        rw = sum(1 for t in removed if float(t["pnl"]) > 0)
        out["variants"][name] = {
            "removed": len(removed),
            "removed_pnl": round(rp, 2),
            "removed_wr": round(rw / len(removed) * 100, 1) if removed else None,
            "kept": len(kept),
            "kept_wr": round(kw / len(kept) * 100, 1) if kept else None,
            "kept_realized": round(kp, 2),
            "equity": round(5000 + kp, 2),
            "delta_equity": round((5000 + kp) - baseline_eq, 2),
        }

    # First burst hour detail for both_green
    burst = [
        t
        for t in trades
        if ensure_utc(t["opened_at"]).hour == 17
        and ensure_utc(t["opened_at"]).day == 31
    ]
    burst_veto = [t for t in burst if variants["both_green"](t)]
    out["burst_31_17utc"] = {
        "n": len(burst),
        "pnl": round(sum(float(t["pnl"]) for t in burst), 2),
        "both_green_n": len(burst_veto),
        "both_green_pnl": round(sum(float(t["pnl"]) for t in burst_veto), 2),
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
