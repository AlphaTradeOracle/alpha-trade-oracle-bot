"""Compare website desk trades since date vs rebuild with trendline gate ON.

Exports:
  - live desk book (default account, opened_at >= since)
  - sim rebuild with SIGNAL_TRENDLINE_GATE enabled (same allowlist / universe)
  - equity curves + blocked / incremental trades

Live ledger is never modified (sim account + reset_ledger).
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


def _load_symbols(path: str | None) -> set[str] | None:
    if not path:
        return None
    out: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line.upper())
    return out


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


def _trade_row(p) -> dict:
    return {
        "id": p.id,
        "symbol": p.symbol,
        "direction": p.direction,
        "status": p.status,
        "score": float(p.signal_score) if p.signal_score is not None else None,
        "signal_id": p.signal_id,
        "entry": float(p.entry_price) if p.entry_price is not None else None,
        "pnl": float(p.realized_pnl or 0),
        "exit_reason": p.exit_reason,
        "notes": (p.notes or "")[:240],
        "opened_at": ensure_utc(p.opened_at).isoformat() if p.opened_at else None,
        "closed_at": ensure_utc(p.closed_at).isoformat() if p.closed_at else None,
    }


def _kpi(trades: list[dict], *, equity: float | None = None, initial: float = 5000.0) -> dict:
    filled = [t for t in trades if t["status"] in {"open", "closed"}]
    closed = [t for t in filled if t["status"] == "closed"]
    open_n = sum(1 for t in filled if t["status"] == "open")
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] < 0]
    gp = sum(t["pnl"] for t in wins)
    gl = sum(t["pnl"] for t in losses)
    pf = (gp / abs(gl)) if gl < 0 else (0.0 if gp == 0 else 99.0)
    realized = sum(t["pnl"] for t in closed)
    return {
        "filled": len(filled),
        "closed": len(closed),
        "open": open_n,
        "win_rate": (len(wins) / len(closed)) if closed else 0.0,
        "profit_factor": pf,
        "realized_pnl": round(realized, 2),
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "equity": round(equity if equity is not None else initial + realized, 2),
        "long_n": sum(1 for t in filled if SignalDirection(t["direction"]).is_long),
        "short_n": sum(1 for t in filled if SignalDirection(t["direction"]).is_short),
    }


async def _load_desk_trades(session, *, since: datetime, account_name: str = "default") -> dict:
    row = (
        await session.execute(
            text(
                """
                select a.id, a.cash_balance, a.realized_pnl, a.initial_balance,
                  a.created_at
                from paper_accounts a where a.name = :name
                """
            ),
            {"name": account_name},
        )
    ).mappings().one()
    account_id = int(row["id"])
    repo = PaperRepository(session)
    positions = await repo.list_positions(account_id)
    since = ensure_utc(since)
    desk = []
    for p in positions:
        if p.status not in {"open", "closed", "pending"}:
            # cancelled retest skips are not on the public desk book
            continue
        opened = ensure_utc(p.opened_at) if p.opened_at else None
        if opened is None or opened < since:
            continue
        desk.append(_trade_row(p))
    desk.sort(key=lambda t: t["opened_at"] or "")

    fills = await repo.list_fills_for_account(account_id)
    fill_rows = [
        (f.filled_at, float(f.pnl), float(f.fee))
        for f in fills
        if ensure_utc(f.filled_at) >= since
    ]
    now = utc_now()
    # Equity curve from initial, but only attribute fills since ``since``.
    # Start equity ≈ initial + realized before since.
    pre = (
        await session.execute(
            text(
                """
                select coalesce(sum(realized_pnl),0) as pre_pnl
                from paper_positions
                where account_id=:aid and status='closed'
                  and closed_at < :since
                """
            ),
            {"aid": account_id, "since": since},
        )
    ).mappings().one()
    initial = float(row["initial_balance"])
    start_equity = initial + float(pre["pre_pnl"] or 0)
    curve = build_equity_curve_points(
        initial=start_equity,
        start_at=since,
        fills=fill_rows,
        as_of=now,
        live_equity=float(row["cash_balance"])
        + sum(
            float(p.margin_used or 0)
            for p in positions
            if p.status == "open"
        ),
    )
    return {
        "account": {
            "id": account_id,
            "cash": float(row["cash_balance"]),
            "realized_pnl": float(row["realized_pnl"]),
            "initial_balance": initial,
            "start_equity_at_since": round(start_equity, 2),
        },
        "trades": desk,
        "kpi": _kpi(desk, equity=None, initial=start_equity),
        "equity_curve": _downsample_curve(curve),
    }


async def _run_sim(
    paper,
    provider,
    *,
    acct_name: str,
    since: datetime,
    symbols: set[str] | None,
    trendline_gate: bool,
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

            trades = [
                _trade_row(p)
                for p in sorted(
                    positions,
                    key=lambda x: ensure_utc(x.opened_at) if x.opened_at else utc_now(),
                )
                if p.status in {"open", "closed", "pending"}
            ]
            fill_rows = [
                (fill.filled_at, float(fill.pnl), float(fill.fee)) for fill in fills
            ]
            now = utc_now()
            start_at = since
            if fill_rows and ensure_utc(fill_rows[0][0]) < start_at:
                start_at = ensure_utc(fill_rows[0][0])
            curve = build_equity_curve_points(
                initial=float(summary.initial_balance),
                start_at=start_at,
                fills=fill_rows,
                as_of=now,
                live_equity=float(summary.equity),
            )
            key = "trendline_on" if trendline_gate else "trendline_off"
            out = {
                "key": key,
                "trendline_gate": trendline_gate,
                "retest_filled": result.retest_filled,
                "retest_skipped": result.retest_skipped,
                "still_open": result.still_open,
                "summary_equity": float(summary.equity),
                "summary_realized": float(summary.realized_pnl),
                "kpi": _kpi(trades, equity=float(summary.equity)),
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
    parser.add_argument(
        "--symbols-file",
        default="scripts/paper_reset_symbols.txt",
        help="Allowlist for sim rebuild; empty string = all symbols",
    )
    parser.add_argument(
        "--all-universe",
        action="store_true",
        help="Sim on all symbols (ignore symbols-file)",
    )
    parser.add_argument("--out", default="/tmp/sim_trendline_vs_desk.json")
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    since = ensure_utc(datetime.fromisoformat(args.since))
    symbols = None if args.all_universe or args.symbols_file == "" else _load_symbols(args.symbols_file)

    container = build_container()
    paper = container.paper_trading
    provider = container.paper_price_provider

    try:
        async with session_scope() as session:
            desk = await _load_desk_trades(session, since=since)

        print(
            f"desk since={since.isoformat()} filled={desk['kpi']['filled']} "
            f"closed={desk['kpi']['closed']} open={desk['kpi']['open']} "
            f"pnl={desk['kpi']['realized_pnl']}",
            flush=True,
        )

        # OFF ≈ current live strategy without new gate; ON = with gate.
        variants = []
        for acct, gate in (("sim_desk_tl_off", False), ("sim_desk_tl_on", True)):
            label = "ON" if gate else "OFF"
            print(f"sim trendline={label} symbols={('ALL' if symbols is None else len(symbols))} ...", flush=True)
            stats = await _run_sim(
                paper,
                provider,
                acct_name=acct,
                since=since,
                symbols=symbols,
                trendline_gate=gate,
            )
            variants.append(stats)
            k = stats["kpi"]
            print(
                f"  equity=${k['equity']:.2f} closed={k['closed']} open={k['open']} "
                f"WR={k['win_rate']:.1%} PF={k['profit_factor']:.2f} "
                f"pnl={k['realized_pnl']}",
                flush=True,
            )

        off = next(v for v in variants if v["key"] == "trendline_off")
        on = next(v for v in variants if v["key"] == "trendline_on")

        desk_filled = {
            t["signal_id"]
            for t in desk["trades"]
            if t["status"] in {"open", "closed"} and t["signal_id"] is not None
        }
        on_filled = {
            t["signal_id"]
            for t in on["trades"]
            if t["status"] in {"open", "closed"} and t["signal_id"] is not None
        }
        off_filled = {
            t["signal_id"]
            for t in off["trades"]
            if t["status"] in {"open", "closed"} and t["signal_id"] is not None
        }

        would_block_from_desk = [
            t
            for t in desk["trades"]
            if t["status"] in {"open", "closed"}
            and t["signal_id"] is not None
            and t["signal_id"] not in on_filled
            and t["signal_id"] in off_filled
        ]
        # Desk trades that sim-ON would not take (even if off also misses — geometry drift)
        desk_not_in_on = [
            t
            for t in desk["trades"]
            if t["status"] in {"open", "closed"}
            and t["signal_id"] is not None
            and t["signal_id"] not in on_filled
        ]
        on_not_in_desk = [
            t
            for t in on["trades"]
            if t["status"] in {"open", "closed"}
            and t["signal_id"] is not None
            and t["signal_id"] not in desk_filled
        ]
        # Trendline-specific skips in ON book (cancelled with reason)
        tl_skips = [
            t
            for t in on["trades"]
            if "broke_" in (t.get("notes") or "")
            or "too_close_" in (t.get("notes") or "")
            or (t.get("exit_reason") == "retest_skipped" and "broke_" in (t.get("notes") or ""))
        ]
        # Also pull cancelled from ON — list_positions may include cancelled if we widen filter.
        # Re-query via off-on signal diff for gate effect.
        blocked_by_gate = [
            t
            for t in off["trades"]
            if t["status"] in {"open", "closed"}
            and t["signal_id"] is not None
            and t["signal_id"] not in on_filled
        ]

        out = {
            "generated_at": utc_now().isoformat(),
            "since": since.isoformat(),
            "symbols_n": None if symbols is None else len(symbols),
            "all_universe": symbols is None,
            "desk": {
                "account": desk["account"],
                "kpi": desk["kpi"],
                "equity_curve": desk["equity_curve"],
                "trades": [t for t in desk["trades"] if t["status"] in {"open", "closed"}],
                "pending": [t for t in desk["trades"] if t["status"] == "pending"],
            },
            "sim_off": {
                "kpi": off["kpi"],
                "retest_filled": off["retest_filled"],
                "retest_skipped": off["retest_skipped"],
                "equity_curve": off["equity_curve"],
                "trades": [t for t in off["trades"] if t["status"] in {"open", "closed"}],
            },
            "sim_on": {
                "kpi": on["kpi"],
                "retest_filled": on["retest_filled"],
                "retest_skipped": on["retest_skipped"],
                "equity_curve": on["equity_curve"],
                "trades": [t for t in on["trades"] if t["status"] in {"open", "closed"}],
            },
            "delta_on_minus_desk": {
                "equity": round(on["kpi"]["equity"] - desk["kpi"]["equity"], 2),
                "realized_pnl": round(
                    on["kpi"]["realized_pnl"] - desk["kpi"]["realized_pnl"], 2
                ),
                "closed": on["kpi"]["closed"] - desk["kpi"]["closed"],
                "filled": on["kpi"]["filled"] - desk["kpi"]["filled"],
            },
            "delta_on_minus_off": {
                "equity": round(on["kpi"]["equity"] - off["kpi"]["equity"], 2),
                "realized_pnl": round(
                    on["kpi"]["realized_pnl"] - off["kpi"]["realized_pnl"], 2
                ),
                "closed": on["kpi"]["closed"] - off["kpi"]["closed"],
                "blocked_n": len(blocked_by_gate),
                "blocked_pnl_if_kept": round(sum(t["pnl"] for t in blocked_by_gate), 2),
            },
            "blocked_by_trendline_vs_off": blocked_by_gate,
            "desk_not_in_sim_on": desk_not_in_on,
            "sim_on_not_in_desk": on_not_in_desk,
            "would_block_from_desk": would_block_from_desk,
        }
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"WROTE {args.out}", flush=True)
        print(
            json.dumps(
                {
                    "desk_kpi": desk["kpi"],
                    "sim_off_kpi": off["kpi"],
                    "sim_on_kpi": on["kpi"],
                    "delta_on_minus_desk": out["delta_on_minus_desk"],
                    "delta_on_minus_off": out["delta_on_minus_off"],
                    "blocked_symbols": [t["symbol"] for t in blocked_by_gate],
                    "desk_not_in_on_n": len(desk_not_in_on),
                    "on_not_in_desk_n": len(on_not_in_desk),
                },
                indent=2,
            ),
            flush=True,
        )
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
