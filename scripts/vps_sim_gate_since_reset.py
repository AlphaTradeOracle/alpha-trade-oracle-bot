"""Compare paper-rebuild gates since reset on sim accounts (live ledger untouched).

Default: current live (L75/S30) vs wide (L60/S40), same since + symbols allowlist
as ``paper rebuild --all-signals --symbols-file paper_reset_symbols.txt``.
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
from app.core.enums import SignalDirection
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
    long_min: float,
    short_max: float,
    since: datetime,
    symbols: set[str] | None,
) -> dict:
    original_goa = paper.get_or_create_account
    orig_long = float(paper._settings.signal_min_score)
    orig_short = float(paper._settings.signal_short_max_score)
    paper._settings.signal_min_score = float(long_min)
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
                    }
                )

            filled = [t for t in trades if t["status"] in {"open", "closed"}]
            long_filled = [
                t
                for t in filled
                if SignalDirection(t["direction"]).is_long
            ]
            short_filled = [
                t
                for t in filled
                if SignalDirection(t["direction"]).is_short
            ]

            out = {
                "key": f"L{long_min:g}_S{short_max:g}",
                "long_min": long_min,
                "short_max": short_max,
                "opened": result.backfill.opened if result.backfill else 0,
                "retest_filled": result.retest_filled,
                "retest_skipped": result.retest_skipped,
                "replayed": result.replayed,
                "still_open": result.still_open,
                "equity": float(summary.equity),
                "cash": float(summary.cash_balance),
                "realized_pnl": float(summary.realized_pnl),
                "closed": int(summary.closed_trades),
                "open": int(summary.open_positions),
                "pending": int(summary.pending_positions),
                "win_rate": float(summary.win_rate),
                "profit_factor": float(summary.profit_factor),
                "total_r": float(summary.total_r),
                "expectancy_r": float(summary.expectancy_r),
                "long_n": len(long_filled),
                "short_n": len(short_filled),
                "long_pnl": round(sum(t["pnl"] for t in long_filled), 2),
                "short_pnl": round(sum(t["pnl"] for t in short_filled), 2),
                "trades": trades,
            }
        finally:
            paper.get_or_create_account = original_goa  # type: ignore[method-assign]
            paper._settings.signal_min_score = orig_long
            paper._settings.signal_short_max_score = orig_short
            await repo.reset_ledger(account)

    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-07-31T16:32:35+00:00")
    parser.add_argument("--symbols-file", default="scripts/paper_reset_symbols.txt")
    parser.add_argument(
        "--all-universe",
        action="store_true",
        help="Ignore symbols file; rebuild on all symbols since reset",
    )
    parser.add_argument("--out", default="/tmp/sim_gate_since_reset.json")
    parser.add_argument(
        "--variants",
        default="live,wide",
        help="Comma list: live=L75/S30, wide=L60/S40, old=L75/S25",
    )
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    since = ensure_utc(datetime.fromisoformat(args.since))
    symbols: set[str] | None
    if args.all_universe:
        symbols = None
    else:
        symbols = _load_symbols(args.symbols_file)
    container = build_container()
    paper = container.paper_trading
    provider = container.paper_price_provider

    catalog = {
        "live": ("sim_reset_L75_S30", 75.0, 30.0),
        "s35": ("sim_reset_L75_S35", 75.0, 35.0),
        "wide": ("sim_reset_L60_S40", 60.0, 40.0),
        "old": ("sim_reset_L75_S25", 75.0, 25.0),
    }
    variants = [catalog[k.strip()] for k in args.variants.split(",") if k.strip() in catalog]

    try:
        async with session_scope() as session:
            live = await _live_snapshot(session)

        sym_n = "ALL" if symbols is None else str(len(symbols))
        print(f"since={since.isoformat()} symbols={sym_n} live={live}", flush=True)
        results = []
        for acct, long_min, short_max in variants:
            print(f"sim L>={long_min} S<={short_max} ...", flush=True)
            stats = await _run_rebuild(
                paper,
                provider,
                acct_name=acct,
                long_min=long_min,
                short_max=short_max,
                since=since,
                symbols=symbols,
            )
            results.append(stats)
            print(
                f"  equity=${stats['equity']:.2f} closed={stats['closed']} "
                f"open={stats['open']} filled={stats['retest_filled']} "
                f"WR={stats['win_rate']:.1%} PF={stats['profit_factor']:.2f} "
                f"L/S={stats['long_n']}/{stats['short_n']} "
                f"pnlL/S={stats['long_pnl']}/{stats['short_pnl']}",
                flush=True,
            )

        by_key = {r["key"]: r for r in results}
        base = by_key.get("L75_S30") or results[0]
        base_sigs = {
            t["signal_id"]
            for t in base["trades"]
            if t["status"] in {"open", "closed"}
        }

        def _incr(target: dict) -> list:
            return [
                t
                for t in target["trades"]
                if t["status"] in {"open", "closed"} and t["signal_id"] not in base_sigs
            ]

        async with session_scope() as session:
            live_after = await _live_snapshot(session)

        comparisons: dict = {}
        for r in results:
            if r["key"] == base["key"]:
                continue
            comparisons[r["key"]] = {
                "equity": round(r["equity"] - base["equity"], 2),
                "realized_pnl": round(r["realized_pnl"] - base["realized_pnl"], 2),
                "closed": r["closed"] - base["closed"],
                "retest_filled": r["retest_filled"] - base["retest_filled"],
                "pf": round(r["profit_factor"] - base["profit_factor"], 3),
                "incremental_n": len(_incr(r)),
            }

        out = {
            "generated_at": utc_now().isoformat(),
            "since": since.isoformat(),
            "symbols_n": None if symbols is None else len(symbols),
            "all_universe": symbols is None,
            "baseline_key": base["key"],
            "live_book": live,
            "live_after_safety": live_after,
            "variants": [
                {k: v for k, v in r.items() if k != "trades"} for r in results
            ],
            "delta_vs_baseline": comparisons,
            "trades_by_key": {
                r["key"]: [t for t in r["trades"] if t["status"] in {"open", "closed"}]
                for r in results
            },
            "incremental_by_key": {
                r["key"]: _incr(r) for r in results if r["key"] != base["key"]
            },
        }
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"WROTE {args.out}", flush=True)
        summary = {
            "live_book_equity": live["cash_balance"],
            "baseline": base["key"],
            "equities": {r["key"]: round(r["equity"], 2) for r in results},
            "delta_vs_baseline": comparisons,
            "live_ok": live_after["closed_n"] == live["closed_n"]
            and live_after["open_n"] == live["open_n"],
        }
        print(json.dumps(summary, indent=2), flush=True)
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
