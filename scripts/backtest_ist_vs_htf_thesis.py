"""Extended IST vs 4h-breakout thesis backtest on ALL DB STRONG signals.

Uses persisted signals (not only paper fills) so the sample is larger than the
paper ledger. Same thesis rule as simulate_htf_breakout_thesis.py:
  - IST: fill at signal entry_mid at created_at
  - Thesis: wait for 4h close beyond lookback high/low (180 bars), structure SL

Outputs JSON summary to stdout. Does not change live strategy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.container import build_container
from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, timeframe_to_timedelta, utc_now
from app.database.session import session_scope
from app.market_data.types import Candle
from app.models.market import Asset, MarketCandle
from app.models.signal import Signal

from pathlib import Path

# Local import of sibling thesis helpers (scripts/ is not a package)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import simulate_htf_breakout_thesis as htf  # noqa: E402

CONFIRM_TF = htf.CONFIRM_TF
EXIT_TF = htf.EXIT_TF
LOOKBACK_4H = htf.LOOKBACK_4H
PENDING_DAYS = htf.PENDING_DAYS
ReplayResult = htf.ReplayResult
TradeInput = htf.TradeInput
_agg = htf._agg
_arm_htf_breakout = htf._arm_htf_breakout
_levels_from_entry_sl = htf._levels_from_entry_sl
_load_candles = htf._load_candles
_replay_from_fill = htf._replay_from_fill
_tf_delta = htf._tf_delta

FEE = Decimal("0.001")
MARGIN = Decimal("100")
LEVERAGE = Decimal("10")


@dataclass
class PairResult:
    signal_id: int
    symbol: str
    direction: str
    score: float
    created_at: str
    paper_linked: bool
    baseline: dict[str, Any] | None
    thesis: dict[str, Any] | None
    thesis_arm: dict[str, Any] | None
    delta: float | None
    skipped: str | None = None


async def main() -> int:
    logging.disable(logging.INFO)
    settings = get_settings()
    configure_logging("ERROR", json_output=False)
    container = build_container(settings)

    rows: list[tuple[Signal, str, bool]] = []
    async with session_scope() as session:
        # All STRONG signals with risk levels
        q = (
            select(Signal, Asset.symbol)
            .join(Asset, Asset.id == Signal.asset_id)
            .where(Signal.direction.in_(("STRONG_LONG", "STRONG_SHORT")))
            .where(Signal.stop_loss.is_not(None))
            .where(Signal.take_profit_1.is_not(None))
            .order_by(Signal.created_at.asc())
        )
        result = await session.execute(q)
        pairs = list(result.all())

        # Which signal_ids have a paper position?
        from app.models.paper import PaperPosition

        paper_ids = set(
            int(x)
            for x in (
                await session.execute(
                    select(PaperPosition.signal_id).where(PaperPosition.signal_id.is_not(None))
                )
            ).scalars()
        )

        for sig, symbol in pairs:
            rows.append((sig, str(symbol), int(sig.id) in paper_ids))

    print(f"STRONG signals with levels: {len(rows)}", file=sys.stderr)

    cache_1h: dict[str, tuple[list[Candle], str]] = {}
    cache_4h: dict[str, tuple[list[Candle], str]] = {}
    baseline_rows: list[ReplayResult] = []
    thesis_rows: list[ReplayResult] = []
    details: list[PairResult] = []

    try:
        async with session_scope() as session:
            for sig, symbol, paper_linked in rows:
                entry_low = float(sig.entry_low) if sig.entry_low is not None else None
                entry_high = float(sig.entry_high) if sig.entry_high is not None else None
                if entry_low is None or entry_high is None:
                    details.append(
                        PairResult(
                            signal_id=int(sig.id),
                            symbol=symbol,
                            direction=sig.direction,
                            score=float(sig.score),
                            created_at=ensure_utc(sig.created_at).isoformat(),
                            paper_linked=paper_linked,
                            baseline=None,
                            thesis=None,
                            thesis_arm=None,
                            delta=None,
                            skipped="no_entry_zone",
                        )
                    )
                    continue
                entry = (entry_low + entry_high) / 2.0
                stop = float(sig.stop_loss)
                tp1 = float(sig.take_profit_1)
                tp2 = float(sig.take_profit_2) if sig.take_profit_2 is not None else tp1
                tp3 = float(sig.take_profit_3) if sig.take_profit_3 is not None else tp2
                opened = ensure_utc(sig.created_at)
                tf = sig.primary_timeframe or "1h"

                trade = TradeInput(
                    id=int(sig.id),
                    symbol=symbol,
                    direction=sig.direction,
                    status="signal",
                    timeframe=tf,
                    entry=entry,
                    stop_loss=stop,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3,
                    qty=float(MARGIN * LEVERAGE / Decimal(str(entry))),
                    notional=float(MARGIN * LEVERAGE),
                    opened_at=opened,
                    expires_at=opened + 4 * _tf_delta(tf),
                    closed_at=None,
                    actual_pnl=0.0,
                    actual_fees=0.0,
                    actual_exit=None,
                    signal_created_at=opened,
                )

                hist_start = utc_now() - timedelta(days=60)
                sym = symbol.upper()
                if sym not in cache_1h:
                    c1, s1 = await _load_candles(
                        session, container.provider, symbol, EXIT_TF, hist_start
                    )
                    cache_1h[sym] = (c1, s1)
                    print(f"  1h {sym}: {len(c1)} ({s1})", file=sys.stderr)
                if sym not in cache_4h:
                    c4, s4 = await _load_candles(
                        session, container.provider, symbol, CONFIRM_TF, hist_start
                    )
                    cache_4h[sym] = (c4, s4)
                    print(f"  4h {sym}: {len(c4)} ({s4})", file=sys.stderr)

                candles_1h, _ = cache_1h[sym]
                candles_4h, _ = cache_4h[sym]
                usable = [c for c in candles_1h if ensure_utc(c.open_time) >= opened]
                if len(usable) < 1 or len(candles_4h) < 15:
                    details.append(
                        PairResult(
                            signal_id=int(sig.id),
                            symbol=symbol,
                            direction=sig.direction,
                            score=float(sig.score),
                            created_at=opened.isoformat(),
                            paper_linked=paper_linked,
                            baseline=None,
                            thesis=None,
                            thesis_arm=None,
                            delta=None,
                            skipped="no_candles",
                        )
                    )
                    continue

                is_long = SignalDirection(sig.direction).is_long
                entry_d = Decimal(str(entry))
                stop_d = Decimal(str(stop))
                tp1_d = Decimal(str(tp1))
                tp2_d = Decimal(str(tp2))
                tp3_d = Decimal(str(tp3))

                baseline = _replay_from_fill(
                    arm="baseline",
                    direction=sig.direction,
                    entry=entry_d,
                    stop=stop_d,
                    tp1=tp1_d,
                    tp2=tp2_d,
                    tp3=tp3_d,
                    fill_time=opened,
                    candles=candles_1h,
                    expiry_at=trade.expires_at,
                )
                baseline_rows.append(baseline)

                arm = _arm_htf_breakout(trade, candles_4h)
                if arm.status != "filled" or arm.fill_price is None or arm.fill_time is None or arm.stop is None:
                    skip = ReplayResult(
                        arm="thesis",
                        pnl=0.0,
                        fees=0.0,
                        exit_reason=arm.status,
                        entry=entry,
                        stop_loss=stop,
                        tp1=tp1,
                        tp2=tp2,
                        tp3=tp3,
                        filled=False,
                        note=arm.note or arm.status,
                    )
                    thesis_rows.append(skip)
                    details.append(
                        PairResult(
                            signal_id=int(sig.id),
                            symbol=symbol,
                            direction=sig.direction,
                            score=float(sig.score),
                            created_at=opened.isoformat(),
                            paper_linked=paper_linked,
                            baseline=asdict(baseline),
                            thesis=asdict(skip),
                            thesis_arm=asdict(arm),
                            delta=round(0.0 - baseline.pnl, 4),
                        )
                    )
                    continue

                fill = Decimal(str(arm.fill_price))
                new_stop = Decimal(str(arm.stop))
                if (is_long and new_stop >= fill) or ((not is_long) and new_stop <= fill):
                    skip = ReplayResult(
                        arm="thesis",
                        pnl=0.0,
                        fees=0.0,
                        exit_reason="skipped_invalid_sl",
                        entry=float(fill),
                        stop_loss=float(new_stop),
                        tp1=tp1,
                        tp2=tp2,
                        tp3=tp3,
                        filled=False,
                        note="invalid_stop",
                    )
                    thesis_rows.append(skip)
                    details.append(
                        PairResult(
                            signal_id=int(sig.id),
                            symbol=symbol,
                            direction=sig.direction,
                            score=float(sig.score),
                            created_at=opened.isoformat(),
                            paper_linked=paper_linked,
                            baseline=asdict(baseline),
                            thesis=asdict(skip),
                            thesis_arm=asdict(arm),
                            delta=round(0.0 - baseline.pnl, 4),
                        )
                    )
                    continue

                ntp1, ntp2, ntp3 = _levels_from_entry_sl(fill, new_stop, is_long)
                thesis_expiry = ensure_utc(arm.fill_time) + 4 * _tf_delta(CONFIRM_TF)
                thesis = _replay_from_fill(
                    arm="thesis",
                    direction=sig.direction,
                    entry=fill,
                    stop=new_stop,
                    tp1=ntp1,
                    tp2=ntp2,
                    tp3=ntp3,
                    fill_time=ensure_utc(arm.fill_time),
                    candles=candles_1h,
                    expiry_at=thesis_expiry,
                )
                thesis_rows.append(thesis)
                details.append(
                    PairResult(
                        signal_id=int(sig.id),
                        symbol=symbol,
                        direction=sig.direction,
                        score=float(sig.score),
                        created_at=opened.isoformat(),
                        paper_linked=paper_linked,
                        baseline=asdict(baseline),
                        thesis=asdict(thesis),
                        thesis_arm=asdict(arm),
                        delta=round(thesis.pnl - baseline.pnl, 4),
                    )
                )
    finally:
        await container.aclose()

    def _with_exits(rows: list[ReplayResult]) -> dict[str, Any]:
        agg = _agg(rows)
        ec: dict[str, int] = {}
        for r in rows:
            ec[r.exit_reason] = ec.get(r.exit_reason, 0) + 1
        agg["exit_counts"] = ec
        return agg

    base_agg = _with_exits(baseline_rows)
    thesis_agg = _with_exits(thesis_rows)
    thesis_filled = _with_exits([r for r in thesis_rows if r.filled])

    helps = hurts = same = 0
    for d in details:
        if d.delta is None:
            continue
        if d.delta > 0.01:
            helps += 1
        elif d.delta < -0.01:
            hurts += 1
        else:
            same += 1

    skip_counts: dict[str, int] = {}
    for r in thesis_rows:
        if not r.filled:
            skip_counts[r.exit_reason] = skip_counts.get(r.exit_reason, 0) + 1

    paper_details = [d for d in details if d.paper_linked and d.baseline]

    def rr(d: dict[str, Any], arm: str) -> ReplayResult:
        return ReplayResult(
            arm=arm,
            pnl=float(d["pnl"]),
            fees=float(d["fees"]),
            exit_reason=str(d["exit_reason"]),
            entry=float(d["entry"]),
            stop_loss=float(d["stop_loss"]),
            tp1=float(d["tp1"]),
            tp2=float(d["tp2"]),
            tp3=float(d["tp3"]),
            bars=int(d.get("bars") or 0),
            tp1_hit=bool(d.get("tp1_hit")),
            tp2_hit=bool(d.get("tp2_hit")),
            tp3_hit=bool(d.get("tp3_hit")),
            filled=bool(d.get("filled", True)),
            note=str(d.get("note") or ""),
        )

    paper_base_rows = [rr(d.baseline, "baseline") for d in paper_details if d.baseline]
    paper_thesis_rows = [rr(d.thesis, "thesis") for d in paper_details if d.thesis]

    ranked = sorted(
        [d for d in details if d.delta is not None],
        key=lambda x: abs(x.delta or 0),
        reverse=True,
    )[:25]

    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "universe": "all STRONG_* signals in DB with SL/TP",
            "ist": "fill entry_mid at signal created_at",
            "thesis": f"4h close beyond {LOOKBACK_4H}-bar lookback high/low, pending {PENDING_DAYS}d",
            "sizing": "$100 margin x10, fee 0.1%, TP 2/4/6R",
            "live_changed": False,
        },
        "sample": {
            "strong_signals": len(rows),
            "simulated": len(baseline_rows),
            "skipped_pre": sum(1 for d in details if d.skipped),
            "paper_linked_simulated": len(paper_base_rows),
            "symbols": sorted({d.symbol for d in details}),
        },
        "ist_all_signals": base_agg,
        "thesis_all_signals": thesis_agg,
        "thesis_filled_only": thesis_filled,
        "delta_total_pnl": round(thesis_agg["total_pnl"] - base_agg["total_pnl"], 2),
        "help_hurt": {"helps": helps, "hurts": hurts, "same": same},
        "skip_counts": skip_counts,
        "paper_subset": {
            "ist": _with_exits(paper_base_rows) if paper_base_rows else {},
            "thesis": _with_exits(paper_thesis_rows) if paper_thesis_rows else {},
            "delta": round(
                (_with_exits(paper_thesis_rows)["total_pnl"] if paper_thesis_rows else 0)
                - (_with_exits(paper_base_rows)["total_pnl"] if paper_base_rows else 0),
                2,
            ),
        },
        "top_abs_deltas": [asdict(d) for d in ranked],
        "verdict_hints": {
            "prefer_filled_metrics": True,
            "skip_zero_inflates_thesis_pnl": True,
        },
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
