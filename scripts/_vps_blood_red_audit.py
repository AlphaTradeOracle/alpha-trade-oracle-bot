"""Audit the blood-red SHORT cluster at paper start (Jul31 evening → Aug1)."""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from app.core.logging import configure_logging
from app.database.session import session_scope

SINCE = datetime(2026, 7, 31, 16, 32, 35, tzinfo=timezone.utc)
UNTIL = datetime(2026, 8, 1, 18, 0, 0, tzinfo=timezone.utc)
BTC_START = SINCE - timedelta(hours=2)
OUT = Path("/tmp/blood_red_audit.json")


async def main() -> None:
    configure_logging("ERROR", json_output=False)
    async with session_scope() as s:
        trades = (
            await s.execute(
                text(
                    """
                    SELECT p.symbol, p.direction, p.status, p.signal_score,
                           p.opened_at, p.closed_at, p.exit_reason,
                           p.realized_pnl, p.entry_price, p.stop_loss,
                           p.market_context
                    FROM paper_positions p
                    JOIN paper_accounts a ON a.id = p.account_id
                    WHERE a.name = 'default'
                      AND p.status = 'closed'
                      AND p.opened_at >= :since
                      AND p.opened_at < :until
                    ORDER BY p.opened_at
                    """
                ),
                {"since": SINCE, "until": UNTIL},
            )
        ).mappings().all()

        rows = []
        for t in trades:
            ctx = t["market_context"] if isinstance(t["market_context"], dict) else {}
            rows.append(
                {
                    "symbol": t["symbol"],
                    "direction": t["direction"],
                    "score": float(t["signal_score"]) if t["signal_score"] is not None else None,
                    "opened_at": t["opened_at"].isoformat(),
                    "closed_at": t["closed_at"].isoformat() if t["closed_at"] else None,
                    "exit_reason": t["exit_reason"],
                    "pnl": float(t["realized_pnl"] or 0),
                    "bias": ctx.get("bias") or ctx.get("biasLabel"),
                    "globalScore": ctx.get("globalScore"),
                    "blend": (ctx.get("blend") or {}).get("finalScore")
                    if isinstance(ctx.get("blend"), dict)
                    else None,
                }
            )

        # BTC move over window (1h closes)
        btc = (
            await s.execute(
                text(
                    """
                    SELECT c.open_time, c.close
                    FROM market_candles c
                    JOIN assets a ON a.id = c.asset_id
                    WHERE a.symbol = 'BTCUSDT' AND c.timeframe = '1h'
                      AND c.open_time >= :btc_start
                      AND c.open_time <= :until
                    ORDER BY c.open_time
                    """
                ),
                {"btc_start": BTC_START, "until": UNTIL},
            )
        ).mappings().all()

        btc_pts = [{"t": r["open_time"].isoformat(), "close": float(r["close"])} for r in btc]
        if btc_pts:
            b0, b1 = btc_pts[0]["close"], btc_pts[-1]["close"]
            btc_ret = (b1 / b0 - 1.0) * 100.0
            # max adverse for shorts = max close / first
            max_c = max(p["close"] for p in btc_pts)
            min_c = min(p["close"] for p in btc_pts)
            btc_pump = (max_c / b0 - 1.0) * 100.0
            btc_dump = (min_c / b0 - 1.0) * 100.0
        else:
            btc_ret = btc_pump = btc_dump = None
            b0 = b1 = None

        wins = [r for r in rows if r["pnl"] > 0]
        losses = [r for r in rows if r["pnl"] < 0]
        flat = [r for r in rows if r["pnl"] == 0]
        by_exit = Counter(r["exit_reason"] or "?" for r in rows)
        by_bias = Counter(str(r["bias"] or "?") for r in rows)
        short_n = sum(1 for r in rows if r["direction"] == "SHORT")
        long_n = sum(1 for r in rows if r["direction"] == "LONG")

        # Hourly open buckets
        hour_pnl: dict[str, dict] = {}
        for r in rows:
            h = r["opened_at"][:13]  # YYYY-MM-DDTHH
            bucket = hour_pnl.setdefault(h, {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0})
            bucket["n"] += 1
            bucket["pnl"] += r["pnl"]
            if r["pnl"] > 0:
                bucket["wins"] += 1
            elif r["pnl"] < 0:
                bucket["losses"] += 1

        # Worst 15
        worst = sorted(rows, key=lambda r: r["pnl"])[:15]

        out = {
            "window": {"since": SINCE.isoformat(), "until": UNTIL.isoformat()},
            "n": len(rows),
            "short_n": short_n,
            "long_n": long_n,
            "wins": len(wins),
            "losses": len(losses),
            "flat": len(flat),
            "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else 0,
            "total_pnl": round(sum(r["pnl"] for r in rows), 2),
            "avg_win": round(sum(r["pnl"] for r in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(r["pnl"] for r in losses) / len(losses), 2) if losses else 0,
            "by_exit": dict(by_exit.most_common()),
            "by_bias": dict(by_bias.most_common()),
            "btc": {
                "bars": len(btc_pts),
                "start": b0,
                "end": b1,
                "return_pct": round(btc_ret, 2) if btc_ret is not None else None,
                "max_pump_pct": round(btc_pump, 2) if btc_pump is not None else None,
                "max_dump_pct": round(btc_dump, 2) if btc_dump is not None else None,
            },
            "hourly": [
                {"hour": h, **{k: (round(v, 2) if k == "pnl" else v) for k, v in d.items()}}
                for h, d in sorted(hour_pnl.items())
            ],
            "worst": worst,
            "score_stats": {
                "min": min((r["score"] for r in rows if r["score"] is not None), default=None),
                "max": max((r["score"] for r in rows if r["score"] is not None), default=None),
                "avg": round(
                    sum(r["score"] for r in rows if r["score"] is not None)
                    / max(1, sum(1 for r in rows if r["score"] is not None)),
                    2,
                ),
            },
        }
        OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
