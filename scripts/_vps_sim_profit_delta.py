"""Sim-only paper rebuilds to attribute equity drop vs ~$6k Jul31 baseline.

Never touches account ``default``. Writes JSON summary to stdout + /tmp.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.container import build_container
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.time import ensure_utc
from app.database.session import session_scope
from app.repositories.paper_repository import PaperRepository
from sqlalchemy import select
from app.models.market import Asset


async def _top400() -> set[str]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Asset.symbol)
                .where(
                    Asset.in_universe.is_(True),
                    Asset.is_active.is_(True),
                    Asset.market_cap_rank.is_not(None),
                )
                .order_by(Asset.market_cap_rank.asc())
                .limit(400)
            )
        ).scalars().all()
    return {str(s).upper() for s in rows}


async def _run(
    paper,
    provider,
    *,
    name: str,
    since: datetime,
    short_max: float,
    symbols: set[str] | None,
) -> dict:
    orig_goa = paper.get_or_create_account
    orig_short = float(paper._settings.signal_short_max_score)
    paper._settings.signal_short_max_score = float(short_max)
    async with session_scope() as session:
        repo = PaperRepository(session)
        account = await repo.get_or_create_account(
            name=name,
            initial_balance=Decimal(str(paper._settings.paper_initial_balance)),
            margin_per_trade=Decimal(str(paper._settings.paper_margin_per_trade)),
            leverage=float(paper._settings.paper_leverage),
        )

        async def _goa(_s):
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
        finally:
            paper.get_or_create_account = orig_goa  # type: ignore[method-assign]
            paper._settings.signal_short_max_score = orig_short

    return {
        "name": name,
        "since": since.isoformat(),
        "short_max": short_max,
        "symbols": "top400" if symbols is not None else "all",
        "equity": round(float(summary.equity), 2),
        "realized": round(float(summary.realized_pnl), 2),
        "closed": int(summary.closed_trades),
        "open": int(summary.open_positions),
        "pending": int(summary.pending_positions),
        "win_rate": round(float(summary.win_rate) * 100, 1),
        "profit_factor": round(float(summary.profit_factor), 3),
        "retest_filled": int(result.retest_filled),
        "retest_skipped": int(result.retest_skipped),
    }


async def main() -> None:
    settings = get_settings()
    configure_logging("WARNING", json_output=False)
    container = build_container(settings)
    paper = container.paper_trading
    provider = container.paper_price_provider
    top400 = await _top400()
    print(f"top400={len(top400)}", flush=True)

    jul31 = datetime(2026, 7, 31, 16, 32, 35, tzinfo=timezone.utc)
    aug1 = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)

    scenarios = [
        ("sim_delta_A_jul31_s30_all", jul31, 30.0, None),
        ("sim_delta_B_jul31_s30_t400", jul31, 30.0, top400),
        ("sim_delta_C_jul31_s29_all", jul31, 29.0, None),
        ("sim_delta_D_aug1_s30_t400", aug1, 30.0, top400),
        ("sim_delta_E_aug1_s29_t400", aug1, 29.0, top400),
        ("sim_delta_F_aug1_s30_all", aug1, 30.0, None),
    ]

    rows: list[dict] = []
    for name, since, smax, syms in scenarios:
        print(f"RUN {name} ...", flush=True)
        row = await _run(
            paper, provider, name=name, since=since, short_max=smax, symbols=syms
        )
        rows.append(row)
        print(json.dumps(row), flush=True)

    base = next(r for r in rows if r["name"].startswith("sim_delta_A_"))
    out = {
        "baseline": base,
        "scenarios": rows,
        "deltas_vs_baseline": [
            {
                "name": r["name"],
                "delta_equity": round(r["equity"] - base["equity"], 2),
                "delta_closed": r["closed"] - base["closed"],
                "equity": r["equity"],
                "short_max": r["short_max"],
                "since": r["since"][:10],
                "symbols": r["symbols"],
            }
            for r in rows
        ],
        "attribution_notes": [
            "A = Jul31 all symbols short_max30 (~prior $6k book)",
            "B isolates Top400 filter on Jul31",
            "C isolates short_max 30→29 on Jul31 all",
            "D/E Aug1 Top400 with short_max 30 vs 29",
            "F Aug1 all symbols short_max30 (window cut only)",
            "MTF/Structure weight changes do NOT affect paper rebuild (stored scores).",
        ],
    }
    Path("/tmp/profit_delta_sims.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    await container.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
