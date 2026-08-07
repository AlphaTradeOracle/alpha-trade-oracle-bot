"""Counterfactual paper rebuild: retest zone/pending params vs live baseline.

Baseline (live): zone_near=0.55, pending×6
Variant:         zone_near=0.45, pending×8

Also runs ablations (0.45×6, 0.55×8) for attribution.
Isolated sim accounts only — default ledger untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.container import build_container
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.repositories.paper_repository import PaperRepository


async def _live_snapshot(session) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                select cash_balance, realized_pnl, initial_balance,
                  (select count(*) from paper_positions p
                   where p.account_id=a.id and p.status='closed') as closed_n,
                  (select count(*) from paper_positions p
                   where p.account_id=a.id and p.status='open') as open_n,
                  (select count(*) from paper_positions p
                   where p.account_id=a.id and p.status='pending') as pending_n
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
    label: str,
    since: datetime,
    zone_near: float,
    zone_far: float,
    pending_mult: int,
) -> dict[str, Any]:
    original_goa = paper.get_or_create_account
    orig_near = float(paper._settings.paper_retest_zone_near)
    orig_far = float(paper._settings.paper_retest_zone_far)
    orig_pend = int(paper._settings.paper_retest_pending_multiplier)

    paper._settings.paper_retest_zone_near = float(zone_near)
    paper._settings.paper_retest_zone_far = float(zone_far)
    paper._settings.paper_retest_pending_multiplier = int(pending_mult)

    async with session_scope() as session:
        repo = PaperRepository(session)
        account = await repo.get_or_create_account(
            name=acct_name,
            initial_balance=Decimal(str(paper._settings.paper_initial_balance)),
            margin_per_trade=Decimal(str(paper._settings.paper_margin_per_trade)),
            leverage=float(paper._settings.paper_leverage),
        )

        async def _goa(_session, *args, **kwargs):
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
                    symbols=None,
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
            out = {
                "key": acct_name,
                "label": label,
                "zone_near": zone_near,
                "zone_far": zone_far,
                "pending_multiplier": pending_mult,
                "opened": result.backfill.opened if result.backfill else 0,
                "retest_filled": result.retest_filled,
                "retest_skipped": result.retest_skipped,
                "retest_still_pending": result.retest_still_pending,
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
                "long_n": sum(1 for t in filled if SignalDirection(t["direction"]).is_long),
                "short_n": sum(1 for t in filled if SignalDirection(t["direction"]).is_short),
                "trades": trades,
            }
        finally:
            paper.get_or_create_account = original_goa  # type: ignore[method-assign]
            paper._settings.paper_retest_zone_near = orig_near
            paper._settings.paper_retest_zone_far = orig_far
            paper._settings.paper_retest_pending_multiplier = orig_pend
            await repo.reset_ledger(account)

    return out


def _delta(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """variant minus baseline."""
    return {
        "equity": round(a["equity"] - b["equity"], 2),
        "realized_pnl": round(a["realized_pnl"] - b["realized_pnl"], 2),
        "closed": a["closed"] - b["closed"],
        "open": a["open"] - b["open"],
        "pending": a["pending"] - b["pending"],
        "retest_filled": a["retest_filled"] - b["retest_filled"],
        "retest_skipped": a["retest_skipped"] - b["retest_skipped"],
        "pf": round(a["profit_factor"] - b["profit_factor"], 3),
        "wr_pp": round((a["win_rate"] - b["win_rate"]) * 100, 1),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-08-05T00:00:00+00:00")
    parser.add_argument("--out", default="/tmp/sim_retest_params.json")
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    since = ensure_utc(datetime.fromisoformat(args.since))
    container = build_container()
    paper = container.paper_trading
    provider = container.paper_price_provider

    variants = [
        ("sim_retest_base_055x6", "Baseline 0.55×6", 0.55, 1.0, 6),
        ("sim_retest_045x8", "Variant 0.45×8", 0.45, 1.0, 8),
        ("sim_retest_045x6", "Ablation 0.45×6", 0.45, 1.0, 6),
        ("sim_retest_055x8", "Ablation 0.55×8", 0.55, 1.0, 8),
    ]

    try:
        async with session_scope() as session:
            live = await _live_snapshot(session)

        print(
            f"since={since.isoformat()} live_cash=${live['cash_balance']:.2f} "
            f"closed={live['closed_n']} open={live['open_n']} pending={live['pending_n']}",
            flush=True,
        )

        results: dict[str, dict[str, Any]] = {}
        for acct, label, near, far, pend in variants:
            print(f"sim {label} ...", flush=True)
            row = await _run_rebuild(
                paper,
                provider,
                acct_name=acct,
                label=label,
                since=since,
                zone_near=near,
                zone_far=far,
                pending_mult=pend,
            )
            results[acct] = row
            print(
                f"  equity=${row['equity']:.2f} closed={row['closed']} open={row['open']} "
                f"pending={row['pending']} filled={row['retest_filled']} "
                f"skipped={row['retest_skipped']} WR={row['win_rate']:.1%} "
                f"PF={row['profit_factor']:.2f}",
                flush=True,
            )

        async with session_scope() as session:
            live_after = await _live_snapshot(session)

        base = results["sim_retest_base_055x6"]
        variant = results["sim_retest_045x8"]
        abl_near = results["sim_retest_045x6"]
        abl_pend = results["sim_retest_055x8"]

        base_ids = {
            t["signal_id"]
            for t in base["trades"]
            if t["status"] in {"open", "closed"} and t["signal_id"]
        }
        incremental = [
            t
            for t in variant["trades"]
            if t["status"] in {"open", "closed"} and t["signal_id"] not in base_ids
        ]

        def _strip(r: dict[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in r.items() if k != "trades"}

        out = {
            "generated_at": utc_now().isoformat(),
            "since": since.isoformat(),
            "test": {
                "baseline": {"zone_near": 0.55, "zone_far": 1.0, "pending_multiplier": 6},
                "variant": {"zone_near": 0.45, "zone_far": 1.0, "pending_multiplier": 8},
            },
            "live_now": live,
            "live_after_safety": live_after,
            "live_untouched": (
                live_after["closed_n"] == live["closed_n"]
                and live_after["open_n"] == live["open_n"]
                and abs(float(live_after["cash_balance"]) - float(live["cash_balance"])) < 0.01
            ),
            "baseline": _strip(base),
            "variant_045x8": _strip(variant),
            "ablation_045x6": _strip(abl_near),
            "ablation_055x8": _strip(abl_pend),
            "delta_variant_minus_baseline": _delta(variant, base),
            "delta_near_only": _delta(abl_near, base),
            "delta_pending_only": _delta(abl_pend, base),
            "incremental_fills": incremental,
            "baseline_trades": [
                t for t in base["trades"] if t["status"] in {"open", "closed", "pending"}
            ],
            "variant_trades": [
                t for t in variant["trades"] if t["status"] in {"open", "closed", "pending"}
            ],
        }
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"WROTE {args.out}", flush=True)
        print(
            json.dumps(
                {
                    "live_cash": live["cash_balance"],
                    "baseline_equity": round(base["equity"], 2),
                    "variant_045x8_equity": round(variant["equity"], 2),
                    "delta": out["delta_variant_minus_baseline"],
                    "near_only": out["delta_near_only"],
                    "pending_only": out["delta_pending_only"],
                    "incremental_fills": len(incremental),
                    "live_untouched": out["live_untouched"],
                },
                indent=2,
            ),
            flush=True,
        )
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
