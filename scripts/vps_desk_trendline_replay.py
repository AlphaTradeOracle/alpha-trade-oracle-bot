"""Replay each desk fill since ``since`` through retest + trendline gate.

Answers: would the new diagonal gate have blocked this website trade?
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.container import build_container
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, timeframe_to_timedelta
from app.database.session import session_scope
from app.models.paper import PaperAccount, PaperPosition
from app.models.signal import Signal
from app.signals.retest_entry import RetestEntryConfig, arm_retest_entry


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-08-01T00:00:00+00:00")
    parser.add_argument("--out", default="/tmp/desk_trendline_replay.json")
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    since = ensure_utc(datetime.fromisoformat(args.since))
    settings = None
    container = build_container()
    settings = container.paper_trading._settings
    provider = container.paper_price_provider

    cfg_on = RetestEntryConfig(
        zone_near=Decimal(str(settings.paper_retest_zone_near)),
        zone_far=Decimal(str(settings.paper_retest_zone_far)),
        pending_multiplier=int(settings.paper_retest_pending_multiplier),
        min_bars_in_zone=int(settings.paper_retest_min_bars_in_zone),
        trendline_gate_enabled=True,
        trendline_buffer_atr=float(settings.signal_trendline_buffer_atr),
        trendline_lookback=int(settings.signal_trendline_lookback),
        trendline_min_points=int(settings.signal_trendline_min_points),
        trendline_min_r2=float(settings.signal_trendline_min_r2),
        trendline_min_clearance_atr=float(settings.signal_trendline_min_clearance_atr),
    )
    cfg_off = RetestEntryConfig(
        zone_near=cfg_on.zone_near,
        zone_far=cfg_on.zone_far,
        pending_multiplier=cfg_on.pending_multiplier,
        min_bars_in_zone=cfg_on.min_bars_in_zone,
        trendline_gate_enabled=False,
    )

    rows: list[dict] = []
    try:
        async with session_scope() as session:
            account = (
                await session.execute(
                    select(PaperAccount).where(PaperAccount.name == "default")
                )
            ).scalar_one()
            positions = (
                await session.execute(
                    select(PaperPosition)
                    .where(
                        PaperPosition.account_id == account.id,
                        PaperPosition.opened_at >= since,
                        PaperPosition.status.in_(("open", "closed")),
                    )
                    .order_by(PaperPosition.opened_at)
                )
            ).scalars().all()

            for pos in positions:
                sig = None
                if pos.signal_id is not None:
                    sig = await session.get(Signal, pos.signal_id)
                if sig is None:
                    rows.append(
                        {
                            "position_id": pos.id,
                            "symbol": pos.symbol,
                            "error": "signal_missing",
                            "desk_pnl": float(pos.realized_pnl or 0),
                        }
                    )
                    continue

                # After fill, position.opened_at is the fill bar — arm at signal time.
                arm_time = ensure_utc(sig.created_at)
                tf = pos.timeframe or getattr(sig, "primary_timeframe", None) or "1h"
                lookback = arm_time - timedelta(days=14)
                end = ensure_utc(pos.closed_at or utc_now_safe())
                end = end + timeframe_to_timedelta(tf) * 2
                try:
                    series = await provider.get_candles(
                        pos.symbol,
                        tf,
                        limit=100_000,
                        start_time=lookback,
                        end_time=end,
                    )
                    candles = list(series.candles) if series is not None else []
                except Exception as exc:  # noqa: BLE001
                    rows.append(
                        {
                            "position_id": pos.id,
                            "symbol": pos.symbol,
                            "error": f"candles:{exc}",
                            "desk_pnl": float(pos.realized_pnl or 0),
                        }
                    )
                    continue

                # Zone-edge reference like paper.
                direction = SignalDirection(pos.direction)
                if direction.is_long:
                    ref = float(
                        getattr(sig, "entry_low", None)
                        or getattr(sig, "entry_price", None)
                        or pos.entry_price
                    )
                else:
                    ref = float(
                        getattr(sig, "entry_high", None)
                        or getattr(sig, "entry_price", None)
                        or pos.entry_price
                    )
                stop = float(
                    getattr(sig, "stop_loss", None)
                    or pos.initial_stop
                    or pos.current_stop
                )

                off = arm_retest_entry(
                    direction=direction,
                    arm_time=arm_time,
                    reference_entry=ref,
                    original_stop=stop,
                    timeframe=tf,
                    candles=candles,
                    config=cfg_off,
                )
                on = arm_retest_entry(
                    direction=direction,
                    arm_time=arm_time,
                    reference_entry=ref,
                    original_stop=stop,
                    timeframe=tf,
                    candles=candles,
                    config=cfg_on,
                )
                rows.append(
                    {
                        "position_id": pos.id,
                        "symbol": pos.symbol,
                        "direction": pos.direction,
                        "signal_id": pos.signal_id,
                        "score": float(pos.signal_score)
                        if pos.signal_score is not None
                        else None,
                        "desk_status": pos.status,
                        "desk_pnl": float(pos.realized_pnl or 0),
                        "desk_exit": pos.exit_reason,
                        "desk_opened_at": ensure_utc(pos.opened_at).isoformat()
                        if pos.opened_at
                        else None,
                        "off_status": off.status,
                        "off_note": off.note,
                        "off_fill": off.fill_price,
                        "on_status": on.status,
                        "on_note": on.note,
                        "on_fill": on.fill_price,
                        "gate_blocks": on.status == "skipped_trendline_break",
                        "gate_reason": on.note
                        if on.status == "skipped_trendline_break"
                        else None,
                    }
                )

        blocked = [r for r in rows if r.get("gate_blocks")]
        kept = [r for r in rows if r.get("off_status") == "filled" and not r.get("gate_blocks")]
        out = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "since": since.isoformat(),
            "n": len(rows),
            "blocked_n": len(blocked),
            "blocked_pnl": round(sum(r["desk_pnl"] for r in blocked), 2),
            "kept_n": len(kept),
            "kept_pnl": round(sum(r["desk_pnl"] for r in kept), 2),
            "counterfactual_desk_pnl": round(
                sum(r["desk_pnl"] for r in rows if not r.get("gate_blocks")), 2
            ),
            "rows": rows,
        }
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps({k: out[k] for k in out if k != "rows"}, indent=2), flush=True)
        for r in rows:
            flag = "BLOCK" if r.get("gate_blocks") else r.get("on_status") or r.get("error")
            print(
                f"{r.get('symbol')} desk={r.get('desk_pnl')} off={r.get('off_status')} "
                f"on={flag} note={r.get('gate_reason') or r.get('on_note')}",
                flush=True,
            )
        print(f"WROTE {args.out}", flush=True)
    finally:
        await container.aclose()


def utc_now_safe():
    from app.core.time import utc_now

    return utc_now()


if __name__ == "__main__":
    asyncio.run(main())
