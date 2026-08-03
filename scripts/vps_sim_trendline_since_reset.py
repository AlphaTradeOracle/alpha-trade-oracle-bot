"""Counterfactual: paper rebuild since reset with/without diagonal trendline gate.

Live ledger untouched. Writes equity curves + blocked-trade diff.

  python scripts/vps_sim_trendline_since_reset.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.charts.paper_equity_chart import build_equity_curve_points
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


def _downsample_curve(
    points: list[tuple[datetime, float]], *, max_points: int = 200
) -> list[dict]:
    if len(points) <= max_points:
        return [
            {"t": ensure_utc(t).isoformat(), "equity": round(float(eq), 2)}
            for t, eq in points
        ]
    step = max(1, len(points) // max_points)
    picked = points[::step]
    if picked[-1] is not points[-1]:
        picked.append(points[-1])
    return [
        {"t": ensure_utc(t).isoformat(), "equity": round(float(eq), 2)}
        for t, eq in picked
    ]


async def _run_rebuild(
    paper,
    provider,
    *,
    acct_name: str,
    trendline_gate: bool,
    since: datetime,
    symbols: set[str] | None,
) -> dict:
    original_goa = paper.get_or_create_account
    orig_gate = bool(paper._settings.signal_trendline_gate_enabled)
    paper._settings.signal_trendline_gate_enabled = bool(trendline_gate)

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
                fills = await repo.list_fills_for_account(account.id)

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
                        "score": float(p.signal_score)
                        if p.signal_score is not None
                        else None,
                        "signal_id": p.signal_id,
                        "entry": float(p.entry_price)
                        if p.entry_price is not None
                        else None,
                        "pnl": float(p.realized_pnl or 0),
                        "exit_reason": p.exit_reason,
                        "opened_at": ensure_utc(p.opened_at).isoformat()
                        if p.opened_at
                        else None,
                        "closed_at": ensure_utc(p.closed_at).isoformat()
                        if p.closed_at
                        else None,
                    }
                )

            fill_rows = [
                (fill.filled_at, float(fill.pnl), float(fill.fee)) for fill in fills
            ]
            now = utc_now()
            start_at = getattr(account, "created_at", None) or since
            if fill_rows and fill_rows[0][0] < start_at:
                start_at = fill_rows[0][0]
            curve = build_equity_curve_points(
                initial=float(summary.initial_balance),
                start_at=start_at,
                fills=fill_rows,
                as_of=now,
                live_equity=float(summary.equity),
            )

            filled = [t for t in trades if t["status"] in {"open", "closed"}]
            skipped = [
                t
                for t in trades
                if t["status"] == "closed"
                and str(t.get("exit_reason") or "").startswith("retest_skipped")
            ]
            long_filled = [
                t for t in filled if SignalDirection(t["direction"]).is_long
            ]
            short_filled = [
                t for t in filled if SignalDirection(t["direction"]).is_short
            ]
            closed = [t for t in filled if t["status"] == "closed"]
            wins = [t for t in closed if t["pnl"] > 0]
            losses = [t for t in closed if t["pnl"] < 0]

            key = "trendline_on" if trendline_gate else "trendline_off"
            out = {
                "key": key,
                "trendline_gate": trendline_gate,
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
                "gross_profit": round(sum(t["pnl"] for t in wins), 2),
                "gross_loss": round(sum(t["pnl"] for t in losses), 2),
                "retest_skipped_rows": len(skipped),
                "equity_curve": _downsample_curve(curve),
                "trades": trades,
            }
        finally:
            paper.get_or_create_account = original_goa  # type: ignore[method-assign]
            paper._settings.signal_trendline_gate_enabled = orig_gate
            await repo.reset_ledger(account)

    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-08-01T00:00:00+00:00")
    parser.add_argument("--symbols-file", default="scripts/paper_reset_symbols.txt")
    parser.add_argument(
        "--all-universe",
        action="store_true",
        help="Ignore symbols file; rebuild on all symbols since reset",
    )
    parser.add_argument("--out", default="/tmp/sim_trendline_since_reset.json")
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

    variants = [
        ("sim_tl_off", False),
        ("sim_tl_on", True),
    ]

    try:
        async with session_scope() as session:
            live = await _live_snapshot(session)

        sym_n = "ALL" if symbols is None else str(len(symbols))
        print(
            f"since={since.isoformat()} symbols={sym_n} live_cash={live['cash_balance']}",
            flush=True,
        )

        results = []
        for acct, gate in variants:
            label = "ON" if gate else "OFF"
            print(f"sim trendline_gate={label} ...", flush=True)
            stats = await _run_rebuild(
                paper,
                provider,
                acct_name=acct,
                trendline_gate=gate,
                since=since,
                symbols=symbols,
            )
            results.append(stats)
            print(
                f"  equity=${stats['equity']:.2f} closed={stats['closed']} "
                f"open={stats['open']} filled={stats['retest_filled']} "
                f"skipped={stats['retest_skipped']} "
                f"WR={stats['win_rate']:.1%} PF={stats['profit_factor']:.2f} "
                f"L/S={stats['long_n']}/{stats['short_n']} "
                f"pnlL/S={stats['long_pnl']}/{stats['short_pnl']}",
                flush=True,
            )

        by_key = {r["key"]: r for r in results}
        off = by_key["trendline_off"]
        on = by_key["trendline_on"]

        off_sigs = {
            t["signal_id"]
            for t in off["trades"]
            if t["status"] in {"open", "closed"} and t["signal_id"] is not None
        }
        on_sigs = {
            t["signal_id"]
            for t in on["trades"]
            if t["status"] in {"open", "closed"} and t["signal_id"] is not None
        }
        blocked = [
            t
            for t in off["trades"]
            if t["status"] in {"open", "closed"}
            and t["signal_id"] is not None
            and t["signal_id"] not in on_sigs
        ]
        added = [
            t
            for t in on["trades"]
            if t["status"] in {"open", "closed"}
            and t["signal_id"] is not None
            and t["signal_id"] not in off_sigs
        ]
        # Same signal may re-open later with different geometry — also show
        # off fills that became retest_skipped under the gate via exit_reason.
        blocked_pnl = round(sum(float(t["pnl"]) for t in blocked), 2)

        async with session_scope() as session:
            live_after = await _live_snapshot(session)

        out = {
            "generated_at": utc_now().isoformat(),
            "since": since.isoformat(),
            "symbols_n": None if symbols is None else len(symbols),
            "all_universe": symbols is None,
            "live_book": live,
            "live_after_safety": live_after,
            "variants": [
                {k: v for k, v in r.items() if k not in {"trades"}} for r in results
            ],
            "delta_on_minus_off": {
                "equity": round(on["equity"] - off["equity"], 2),
                "realized_pnl": round(on["realized_pnl"] - off["realized_pnl"], 2),
                "closed": on["closed"] - off["closed"],
                "retest_filled": on["retest_filled"] - off["retest_filled"],
                "retest_skipped": on["retest_skipped"] - off["retest_skipped"],
                "pf": round(on["profit_factor"] - off["profit_factor"], 3),
                "win_rate": round(on["win_rate"] - off["win_rate"], 4),
                "blocked_n": len(blocked),
                "blocked_pnl_if_kept": blocked_pnl,
                "added_n": len(added),
            },
            "blocked_trades": blocked,
            "added_trades": added,
            "trades_by_key": {
                r["key"]: [
                    t for t in r["trades"] if t["status"] in {"open", "closed"}
                ]
                for r in results
            },
        }
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"WROTE {args.out}", flush=True)
        print(
            json.dumps(
                {
                    "live_ok": live_after["closed_n"] == live["closed_n"]
                    and live_after["open_n"] == live["open_n"],
                    "off_equity": round(off["equity"], 2),
                    "on_equity": round(on["equity"], 2),
                    "delta": out["delta_on_minus_off"],
                    "blocked_n": len(blocked),
                    "blocked_symbols": [t["symbol"] for t in blocked],
                },
                indent=2,
            ),
            flush=True,
        )
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
