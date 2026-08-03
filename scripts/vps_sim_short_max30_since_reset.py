"""Simulate paper rebuild since reset with short_max=30 (sim account only).

Mirrors ``paper rebuild --since 2026-07-31T16:32:35 --all-signals --symbols-file
paper_reset_symbols.txt`` but overrides SIGNAL_SHORT_MAX_SCORE. Never touches
the live ``default`` account.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.container import build_container
from app.core.logging import configure_logging
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.repositories.paper_repository import PaperRepository


def _load_symbols(path: str) -> set[str]:
    out: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line.upper())
    return out


async def _live_snapshot(session) -> dict:
    row = (
        await session.execute(
            text(
                """
                select cash_balance, realized_pnl, initial_balance,
                  (select count(*) from paper_positions p
                   where p.account_id=a.id and p.status='closed') as closed_n,
                  (select count(*) from paper_positions p
                   where p.account_id=a.id and p.status='open') as open_n
                from paper_accounts a where name='default'
                """
            )
        )
    ).mappings().one()
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in dict(row).items()}


async def _run_rebuild(
    paper,
    provider,
    *,
    acct_name: str,
    short_max: float,
    since: datetime,
    symbols: set[str],
) -> dict:
    original_goa = paper.get_or_create_account
    original_short_max = float(paper._settings.signal_short_max_score)
    paper._settings.signal_short_max_score = float(short_max)

    async with session_scope() as session:
        repo = PaperRepository(session)
        account = await repo.get_or_create_account(
            name=acct_name,
            initial_balance=Decimal(str(paper._settings.paper_initial_balance)),
            margin_per_trade=Decimal(str(paper._settings.paper_margin_per_trade)),
            leverage=float(paper._settings.paper_leverage),
        )

        async def _goa(_session):
            return account

        paper.get_or_create_account = _goa  # type: ignore[method-assign]
        try:
            with paper._without_notifications():
                result = await paper.rebuild_from_signals(
                    session,
                    since=since,
                    provider=provider,
                    providers=None,
                    dispatched_only=False,
                    one_per_symbol=False,
                    symbols=symbols,
                )
                summary = await paper.summary(session)
                positions = await repo.list_positions(account.id)

            trades = []
            for p in sorted(
                positions,
                key=lambda x: ensure_utc(x.opened_at) if x.opened_at else utc_now(),
            ):
                trades.append(
                    {
                        "id": p.id,
                        "symbol": p.symbol,
                        "direction": p.direction,
                        "status": p.status,
                        "score": float(p.signal_score) if p.signal_score is not None else None,
                        "signal_id": p.signal_id,
                        "entry": float(p.entry_price) if p.entry_price is not None else None,
                        "pnl": float(p.realized_pnl or 0),
                        "exit_reason": p.exit_reason,
                        "opened_at": ensure_utc(p.opened_at).isoformat() if p.opened_at else None,
                        "closed_at": ensure_utc(p.closed_at).isoformat() if p.closed_at else None,
                        "notes": (p.notes or "")[:120],
                    }
                )

            out = {
                "short_max": short_max,
                "reset_positions": result.reset_positions,
                "opened": result.backfill.opened if result.backfill else 0,
                "retest_filled": result.retest_filled,
                "retest_skipped": result.retest_skipped,
                "retest_still_pending": result.retest_still_pending,
                "replayed": result.replayed,
                "still_open": result.still_open,
                "equity": float(summary.equity),
                "cash": float(summary.cash_balance),
                "realized_pnl": float(summary.realized_pnl),
                "unrealized_pnl": float(summary.equity - summary.cash_balance),
                "closed": int(summary.closed_trades),
                "open": int(summary.open_positions),
                "pending": int(summary.pending_positions),
                "win_rate": float(summary.win_rate),
                "profit_factor": float(summary.profit_factor),
                "total_r": float(summary.total_r),
                "expectancy_r": float(summary.expectancy_r),
                "trades": trades,
            }
        finally:
            paper.get_or_create_account = original_goa  # type: ignore[method-assign]
            paper._settings.signal_short_max_score = original_short_max
            # Keep sim rows for inspection; wipe cash noise via reset
            await repo.reset_ledger(account)

    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-07-31T16:32:35+00:00")
    parser.add_argument(
        "--symbols-file",
        default="scripts/paper_reset_symbols.txt",
    )
    parser.add_argument("--out", default="/tmp/sim_short_max30_since_reset.json")
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    since = ensure_utc(datetime.fromisoformat(args.since))
    symbols = _load_symbols(args.symbols_file)
    container = build_container()
    paper = container.paper_trading
    provider = container.paper_price_provider

    try:
        async with session_scope() as session:
            live_before = await _live_snapshot(session)

        print(f"since={since.isoformat()} symbols={len(symbols)}", flush=True)
        print(f"live_before={live_before}", flush=True)

        print("sim short_max=25 (baseline rebuild)...", flush=True)
        base = await _run_rebuild(
            paper,
            provider,
            acct_name="sim_reset_s25",
            short_max=25.0,
            since=since,
            symbols=symbols,
        )
        print(
            f"  s25 equity={base['equity']:.2f} closed={base['closed']} "
            f"open={base['open']} filled={base['retest_filled']} pnl={base['realized_pnl']:.2f}",
            flush=True,
        )

        print("sim short_max=30 ...", flush=True)
        s30 = await _run_rebuild(
            paper,
            provider,
            acct_name="sim_reset_s30",
            short_max=30.0,
            since=since,
            symbols=symbols,
        )
        print(
            f"  s30 equity={s30['equity']:.2f} closed={s30['closed']} "
            f"open={s30['open']} filled={s30['retest_filled']} pnl={s30['realized_pnl']:.2f}",
            flush=True,
        )

        base_ids = {
            (t["signal_id"], t["symbol"], t["opened_at"])
            for t in base["trades"]
            if t["status"] in {"open", "closed"}
        }
        incremental = [
            t
            for t in s30["trades"]
            if t["status"] in {"open", "closed"}
            and (t["signal_id"], t["symbol"], t["opened_at"]) not in base_ids
            and t.get("score") is not None
            and 25.0 < float(t["score"]) <= 30.0
        ]
        # also any s30 filled trade not in baseline by signal_id
        base_sigs = {t["signal_id"] for t in base["trades"] if t["status"] in {"open", "closed"}}
        incremental_by_sig = [
            t
            for t in s30["trades"]
            if t["status"] in {"open", "closed"} and t["signal_id"] not in base_sigs
        ]

        async with session_scope() as session:
            live_after = await _live_snapshot(session)

        out = {
            "generated_at": utc_now().isoformat(),
            "since": since.isoformat(),
            "symbols_n": len(symbols),
            "symbols": sorted(symbols),
            "live_book": live_before,
            "live_after_safety": live_after,
            "baseline_short_max_25": {k: v for k, v in base.items() if k != "trades"},
            "short_max_30": {k: v for k, v in s30.items() if k != "trades"},
            "delta_vs_s25": {
                "equity": round(s30["equity"] - base["equity"], 2),
                "realized_pnl": round(s30["realized_pnl"] - base["realized_pnl"], 2),
                "closed": s30["closed"] - base["closed"],
                "open": s30["open"] - base["open"],
                "retest_filled": s30["retest_filled"] - base["retest_filled"],
            },
            "delta_vs_live_book": {
                "equity": round(s30["equity"] - float(live_before["cash_balance"]), 2),
                "note": "live book has no open marks; cash≈equity when flat",
            },
            "baseline_trades": base["trades"],
            "short30_trades": s30["trades"],
            "incremental_trades_score_25_30": incremental,
            "incremental_trades_not_in_s25": incremental_by_sig,
        }
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"WROTE {args.out}", flush=True)
        print(
            json.dumps(
                {
                    "live_cash": live_before["cash_balance"],
                    "s25_equity": base["equity"],
                    "s30_equity": s30["equity"],
                    "s30_closed": s30["closed"],
                    "s30_open": s30["open"],
                    "incremental_n": len(incremental_by_sig),
                    "live_safety_ok": live_after == live_before
                    or (
                        live_after["closed_n"] == live_before["closed_n"]
                        and live_after["open_n"] == live_before["open_n"]
                    ),
                },
                indent=2,
            ),
            flush=True,
        )
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
