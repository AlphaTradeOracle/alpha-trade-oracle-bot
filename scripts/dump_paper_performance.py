"""One-off dump of all paper positions as JSON to stdout."""
from __future__ import annotations

import asyncio
import json
import logging

from app.container import build_container
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import session_scope
from app.repositories.paper_repository import PaperRepository


async def main() -> int:
    logging.disable(logging.CRITICAL)
    settings = get_settings()
    configure_logging("ERROR", json_output=False)
    c = build_container(settings)
    try:
        async with session_scope() as session:
            acct = await c.paper_trading.get_or_create_account(session)
            repo = PaperRepository(session)
            positions = await repo.list_positions(acct.id)
            open_p = [p for p in positions if p.status == "open"]
            closed = [p for p in positions if p.status == "closed"]

            def row(p):
                return {
                    "id": int(p.id),
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "tf": p.timeframe or "1h",
                    "entry": float(p.entry_price),
                    "sl": float(p.stop_loss),
                    "tp1": float(p.take_profit_1),
                    "tp2": float(p.take_profit_2),
                    "tp3": float(p.take_profit_3),
                    "opened_at": p.opened_at.isoformat() if p.opened_at else None,
                    "closed_at": p.closed_at.isoformat() if p.closed_at else None,
                    "exit_reason": p.exit_reason,
                    "realized_pnl": float(p.realized_pnl or 0),
                    "fees": float(p.fees or 0),
                }

            wins = [p for p in closed if float(p.realized_pnl or 0) > 0]
            losses = [p for p in closed if float(p.realized_pnl or 0) < 0]
            gw = sum(float(p.realized_pnl) for p in wins)
            gl = abs(sum(float(p.realized_pnl) for p in losses))
            exits: dict[str, int] = {}
            exit_pnl: dict[str, float] = {}
            for p in closed:
                k = p.exit_reason or "unknown"
                exits[k] = exits.get(k, 0) + 1
                exit_pnl[k] = exit_pnl.get(k, 0.0) + float(p.realized_pnl or 0)

            cash = float(acct.cash_balance)
            start = float(getattr(acct, "initial_balance", None) or 5000)
            realized = float(acct.realized_pnl or 0)
            margin = 100.0 * len(open_p)
            out = {
                "cash": cash,
                "start": start,
                "realized": realized,
                "equity_approx": cash + margin,
                "book": start + realized,
                "open_n": len(open_p),
                "closed_n": len(closed),
                "total_n": len(positions),
                "wr": round(len(wins) / len(closed), 4) if closed else 0,
                "wins": len(wins),
                "losses": len(losses),
                "pf": round(gw / gl, 4) if gl > 0 else (99.0 if gw > 0 else 0.0),
                "closed_rpnl": round(sum(float(p.realized_pnl or 0) for p in closed), 2),
                "fees": round(sum(float(p.fees or 0) for p in positions), 2),
                "exits": exits,
                "exit_pnl": {k: round(v, 2) for k, v in exit_pnl.items()},
                "open": [row(p) for p in sorted(open_p, key=lambda x: x.opened_at or x.id)],
                "closed": [
                    row(p)
                    for p in sorted(
                        closed,
                        key=lambda x: x.closed_at or x.opened_at or x.id,
                        reverse=True,
                    )
                ],
            }
            print(json.dumps(out, default=str))
    finally:
        await c.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
