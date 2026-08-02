"""Audit: actionable in-universe signals since paper reset vs paper ledger.

Reports per-symbol first opportunity and why later signals were skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.container import build_container
from app.core.logging import configure_logging
from app.core.time import ensure_utc
from app.database.session import session_scope
from app.models.market import Asset
from app.models.paper import PaperPosition
from app.repositories.asset_repository import AssetRepository
from app.core.enums import SignalDirection
from app.repositories.signal_repository import SignalRepository
async def run(*, since: datetime, dispatched_only: bool) -> int:
    from datetime import UTC, timedelta

    container = build_container()
    configure_logging(container.settings.log_level, json_output=False)
    paper = container.paper_trading

    async with session_scope() as session:
        universe = {
            str(s).upper()
            for s in (
                await session.execute(select(Asset.symbol).where(Asset.in_universe.is_(True)))
            ).scalars()
        }
        account = await paper.get_or_create_account(session)

        signals = await SignalRepository(session).list_since(
            since,
            actionable_only=True,
            dispatched_only=dispatched_only,
            limit=20_000,
        )
        asset_ids = list({s.asset_id for s in signals})
        symbols_by_id = await AssetRepository(session).get_symbols_by_ids(asset_ids)
        ordered = sorted(signals, key=lambda s: s.created_at)

        positions = list(
            (
                await session.execute(
                    select(PaperPosition).where(PaperPosition.account_id == account.id)
                )
            ).scalars()
        )
        paper_by_symbol: dict[str, list[PaperPosition]] = defaultdict(list)
        for p in positions:
            paper_by_symbol[p.symbol.upper()].append(p)

        # Fresh chronological simulation (do not pre-seed locks from ledger).
        free_at: dict[str, datetime] = {}
        accepted: list[dict] = []
        rejected: list[dict] = []
        gate_reasons: dict[str, int] = defaultdict(int)

        def gate_fail_reason(signal) -> str | None:
            try:
                direction = SignalDirection(signal.direction)
            except ValueError:
                return "bad_direction"
            settings = paper._settings
            if settings.signal_require_strong and direction not in {
                SignalDirection.STRONG_LONG,
                SignalDirection.STRONG_SHORT,
            }:
                return "require_strong"
            if direction.is_long and float(signal.score) < settings.signal_min_score:
                return "long_score"
            if direction.is_short and float(signal.score) > settings.signal_short_max_score:
                return "short_score_high"
            if direction.is_short and float(signal.score) <= settings.signal_short_min_score:
                return "short_score_low"
            if signal.stop_loss is None or signal.take_profit_1 is None:
                return "missing_levels"
            if signal.take_profit_2 is None or signal.take_profit_3 is None:
                return "missing_levels"
            rr = float(signal.risk_reward_ratio or 0.0)
            if rr < settings.min_risk_reward_ratio:
                return "rr"
            if float(signal.data_quality) < 60.0:
                return "data_quality"
            return None

        for signal in ordered:
            symbol = symbols_by_id.get(signal.asset_id)
            if not symbol:
                rejected.append({"symbol": "?", "reason": "no_symbol", "at": signal.created_at})
                continue
            symbol = symbol.upper()
            at = ensure_utc(signal.created_at)
            if symbol not in universe:
                rejected.append(
                    {
                        "symbol": symbol,
                        "reason": "not_in_universe",
                        "at": signal.created_at,
                        "dir": signal.direction,
                        "score": signal.score,
                        "sid": signal.id,
                    }
                )
                continue
            fail = gate_fail_reason(signal)
            if fail:
                gate_reasons[fail] += 1
                rejected.append(
                    {
                        "symbol": symbol,
                        "reason": f"paper_gates:{fail}",
                        "at": signal.created_at,
                        "dir": signal.direction,
                        "score": signal.score,
                        "sid": signal.id,
                    }
                )
                continue
            until = free_at.get(symbol)
            if until is not None and at < until:
                rejected.append(
                    {
                        "symbol": symbol,
                        "reason": "symbol_busy",
                        "at": signal.created_at,
                        "dir": signal.direction,
                        "score": signal.score,
                        "sid": signal.id,
                    }
                )
                continue

            paper_rows = paper_by_symbol.get(symbol, [])
            matched = next(
                (
                    p
                    for p in paper_rows
                    if p.signal_id == signal.id
                    or (
                        ensure_utc(p.opened_at) >= at - timedelta(minutes=5)
                        and ensure_utc(p.opened_at) <= at + timedelta(hours=8)
                    )
                ),
                None,
            )
            accepted.append(
                {
                    "symbol": symbol,
                    "at": signal.created_at,
                    "dir": signal.direction,
                    "score": signal.score,
                    "sid": signal.id,
                    "dispatched": bool(signal.is_dispatched),
                    "paper_status": matched.status if matched else None,
                    "paper_exit": matched.exit_reason if matched else None,
                    "paper_statuses": [p.status for p in paper_rows],
                }
            )
            if matched is not None:
                if matched.status in ("open", "pending"):
                    free_at[symbol] = datetime.max.replace(tzinfo=UTC)
                elif matched.closed_at is not None:
                    free_at[symbol] = ensure_utc(matched.closed_at)
                else:
                    free_at[symbol] = at + timedelta(hours=6)
            else:
                # No ledger row — still lock so we don't double-count misses
                free_at[symbol] = at + timedelta(hours=6)

        # Missing: first eligible signal per symbol with no paper row at all
        first_eligible: dict[str, dict] = {}
        for row in accepted:
            first_eligible.setdefault(row["symbol"], row)

        missing = [
            row
            for sym, row in sorted(first_eligible.items())
            if not paper_by_symbol.get(sym)
        ]
        orphan_paper = sorted(
            sym for sym in paper_by_symbol if sym not in first_eligible and sym in universe
        )

        print(f"since={since.isoformat()} universe={len(universe)} dispatched_only={dispatched_only}")
        print(f"actionable_signals={len(ordered)}")
        print(f"eligible_after_gates_and_busy={len(accepted)} symbols={len(first_eligible)}")
        print(f"rejected={len(rejected)}")
        reasons: dict[str, int] = defaultdict(int)
        for r in rejected:
            reasons[r["reason"]] += 1
        for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  reject_{k}={v}")
        if gate_reasons:
            print("  gate detail:")
            for k, v in sorted(gate_reasons.items(), key=lambda kv: -kv[1]):
                print(f"    {k}={v}")

        print(f"\n--- first eligible per symbol ({len(first_eligible)}) ---")
        for sym, row in sorted(first_eligible.items(), key=lambda kv: kv[1]["at"]):
            statuses = ",".join(row["paper_statuses"]) or "-"
            pst = row.get("paper_status") or "-"
            if not paper_by_symbol.get(sym):
                flag = "MISSING"
            elif row.get("paper_status"):
                flag = "OK"
            else:
                flag = "GAP"  # symbol has paper but not for this first eligible
            print(
                f"{flag:7} {sym:12} {row['at'].isoformat()} {row['dir']:12} "
                f"score={row['score']:.1f} sid={row['sid']} "
                f"matched={pst} paper=[{statuses}]"
            )

        print(f"\n--- MISSING paper for eligible symbol ({len(missing)}) ---")
        if not missing:
            print("(none)")
        for row in missing:
            print(
                f"MISSING {row['symbol']:12} {row['at'].isoformat()} "
                f"{row['dir']:12} score={row['score']:.1f} sid={row['sid']}"
            )

        gaps = [
            row
            for sym, row in first_eligible.items()
            if paper_by_symbol.get(sym) and not row.get("paper_status")
        ]
        print(f"\n--- GAP: eligible but matched paper row unclear ({len(gaps)}) ---")
        for row in gaps:
            print(
                f"GAP     {row['symbol']:12} {row['at'].isoformat()} "
                f"sid={row['sid']} paper={row['paper_statuses']}"
            )

        print(f"\n--- paper symbols without eligible signal in window ({len(orphan_paper)}) ---")
        print(", ".join(orphan_paper) if orphan_paper else "(none)")

        print("\n--- current paper book ---")
        for p in sorted(positions, key=lambda x: ensure_utc(x.opened_at or x.created_at)):
            print(
                f"{p.symbol:12} {p.status:10} {p.direction:12} "
                f"exit={p.exit_reason or '-'} opened={p.opened_at}"
            )

    await container.aclose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-07-31T16:32:35+00:00")
    parser.add_argument("--dispatched-only", action="store_true")
    args = parser.parse_args()
    since = ensure_utc(datetime.fromisoformat(args.since))
    raise SystemExit(asyncio.run(run(since=since, dispatched_only=args.dispatched_only)))


if __name__ == "__main__":
    main()
