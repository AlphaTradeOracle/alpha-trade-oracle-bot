"""Counterfactual SL / retest-ATR sweep on paper closed + pending.

Six settings (ATR stop multiplier + retest zone near/far):
  1. baseline   — SL 1.5×ATR · zone 0.55–1.0  (live)
  2. sl_tight   — SL 1.2×ATR · zone 0.55–1.0
  3. sl_wide18  — SL 1.8×ATR · zone 0.55–1.0
  4. sl_wide20  — SL 2.0×ATR · zone 0.55–1.0
  5. zone_wide  — SL 1.5×ATR · zone 0.35–1.2
  6. combo      — SL 1.8×ATR · zone 0.40–1.15

Closed: rebuild stop from entry ATR, resize qty to keep $risk, OHLC replay.
Pending: re-arm retest with new zone + new orig SL from ref; report fill rate
         and sim PnL if filled (separate bucket).

    python scripts/simulate_sl_atr_variants.py --out /tmp/sl_atr_variants.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, timeframe_to_timedelta, utc_now
from app.database.session import session_scope
from app.models.paper import PaperAccount, PaperPosition
from app.models.signal import Signal
from app.repositories.asset_repository import AssetRepository
from app.signals.retest_entry import (
    RetestEntryConfig,
    arm_retest_entry,
    levels_from_entry_sl,
    wilder_atr,
)
from app.market_data.types import Candle

_ATR_NOTE = re.compile(r"(?:^|;)atr=(?P<v>[0-9.eE+-]+)")
_REF_NOTE = re.compile(r"ref_entry=(?P<v>[0-9.eE+-]+)")
_ORIG_SL = re.compile(r"orig_sl=(?P<v>[0-9.eE+-]+)")
_ARMED = re.compile(r"armed_at=(?P<v>[^;]+)")

SETTINGS: list[dict[str, Any]] = [
    {
        "key": "baseline",
        "label": "Baseline (live)",
        "sl_atr": 1.5,
        "zone_near": 0.55,
        "zone_far": 1.0,
    },
    {
        "key": "sl_tight_1.2",
        "label": "SL tighter 1.2×ATR",
        "sl_atr": 1.2,
        "zone_near": 0.55,
        "zone_far": 1.0,
    },
    {
        "key": "sl_wide_1.8",
        "label": "SL wider 1.8×ATR",
        "sl_atr": 1.8,
        "zone_near": 0.55,
        "zone_far": 1.0,
    },
    {
        "key": "sl_wide_2.0",
        "label": "SL wider 2.0×ATR",
        "sl_atr": 2.0,
        "zone_near": 0.55,
        "zone_far": 1.0,
    },
    {
        "key": "zone_wide",
        "label": "Zone wider 0.35–1.2",
        "sl_atr": 1.5,
        "zone_near": 0.35,
        "zone_far": 1.2,
    },
    {
        "key": "combo_1.8_z040",
        "label": "Combo SL1.8 + zone 0.40–1.15",
        "sl_atr": 1.8,
        "zone_near": 0.40,
        "zone_far": 1.15,
    },
]

SCALE = (0.5, 0.25, 0.25)
TP_MULTS = (1.5, 2.5, 4.0)


@dataclass
class TradeRow:
    id: int
    symbol: str
    direction: str
    status: str
    entry: float | None
    stop: float | None
    qty: float
    risk_usd: float
    opened_at: datetime | None
    closed_at: datetime | None
    exit_reason: str | None
    actual_pnl: float
    score: float | None
    timeframe: str
    notes: str
    signal_id: Any
    ref_entry: float | None
    orig_sl: float | None
    atr_note: float | None
    armed_at: datetime | None
    signal_created: datetime | None
    signal_entry_low: float | None
    signal_entry_high: float | None
    signal_stop: float | None
    signal_ref: float | None


def _parse_notes(notes: str | None) -> dict[str, Any]:
    text = notes or ""
    out: dict[str, Any] = {
        "atr": None,
        "ref": None,
        "orig_sl": None,
        "armed_at": None,
    }
    m = _ATR_NOTE.search(text)
    if m:
        out["atr"] = float(m.group("v"))
    m = _REF_NOTE.search(text)
    if m:
        out["ref"] = float(m.group("v"))
    m = _ORIG_SL.search(text)
    if m:
        out["orig_sl"] = float(m.group("v"))
    m = _ARMED.search(text)
    if m:
        try:
            raw = m.group("v").strip()
            out["armed_at"] = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            pass
    return out


def _dir_long(direction: str) -> bool:
    return "LONG" in direction.upper()


def _candles_from_df(df: Any, timeframe: str) -> list[Candle]:
    delta = timeframe_to_timedelta(timeframe)
    rows: list[Candle] = []
    for ts, row in df.iterrows():
        ot = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        ot = ensure_utc(ot)
        rows.append(
            Candle(
                open_time=ot,
                close_time=ot + delta,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0),
                quote_volume=float(row.get("quote_volume") or 0),
                trade_count=int(row.get("trade_count") or 0),
                is_closed=True,
            )
        )
    return rows


def _idx_at(candles: list[Candle], when: datetime) -> int | None:
    when = ensure_utc(when)
    idx = None
    for i, c in enumerate(candles):
        if ensure_utc(c.open_time) <= when:
            idx = i
        else:
            break
    return idx


def _new_stop(entry: float, atr: float, *, is_long: bool, sl_atr: float) -> float:
    dist = atr * sl_atr
    return entry - dist if is_long else entry + dist


def _qty_for_risk(entry: float, stop: float, risk_usd: float) -> float:
    dist = abs(entry - stop)
    if dist <= 1e-12 or risk_usd <= 0:
        return 0.0
    return risk_usd / dist


def _replay(
    *,
    is_long: bool,
    entry: float,
    stop: float,
    qty: float,
    candles: list[Candle],
    entry_at: datetime,
    fee_pct: float,
    move_be: bool,
    end_at: datetime | None,
) -> dict[str, Any]:
    """Scale-out TP replay; stop-first; BE after TP1."""
    if qty <= 0 or abs(entry - stop) <= 1e-12:
        return {"pnl": 0.0, "fees": 0.0, "exit": "no_size", "r": 0.0, "bars": 0}

    tp1, tp2, tp3 = levels_from_entry_sl(
        Decimal(str(entry)),
        Decimal(str(stop)),
        is_long=is_long,
        multipliers=tuple(Decimal(str(m)) for m in TP_MULTS),
    )
    tp1, tp2, tp3 = float(tp1), float(tp2), float(tp3)
    risk_dist = abs(entry - stop)
    qty0 = qty
    rem = qty0
    realized = 0.0
    fees = abs(entry * qty0) * (fee_pct / 100.0)
    realized -= fees
    cur_stop = stop
    tp1_hit = tp2_hit = tp3_hit = False
    bars = 0
    exit_reason = "open"
    entry_at = ensure_utc(entry_at)
    end_at = ensure_utc(end_at) if end_at else None
    last_close = entry

    def _reduce(price: float, frac: float | None, reason: str, all_rest: bool = False) -> None:
        nonlocal rem, realized, fees, exit_reason
        if rem <= 1e-12:
            return
        q = rem if all_rest or frac is None else min(qty0 * frac, rem)
        if q <= 0:
            return
        direction = 1.0 if is_long else -1.0
        gross = (price - entry) * q * direction
        fee = abs(price * q) * (fee_pct / 100.0)
        realized += gross - fee
        fees += fee
        rem -= q
        exit_reason = reason
        if rem < 1e-12:
            rem = 0.0

    for c in candles:
        when = ensure_utc(c.open_time)
        if when < entry_at:
            continue
        if end_at and when > end_at:
            break
        if rem <= 0:
            break
        bars += 1
        high, low = float(c.high), float(c.low)
        last_close = float(c.close)
        stop_hit = low <= cur_stop if is_long else high >= cur_stop
        if stop_hit:
            _reduce(cur_stop, None, "stop_loss" if abs(cur_stop - entry) > 1e-12 else "break_even", True)
            break
        if not tp1_hit:
            hit = high >= tp1 if is_long else low <= tp1
            if hit:
                _reduce(tp1, SCALE[0], "take_profit_1")
                tp1_hit = True
                if move_be:
                    cur_stop = entry
                continue
        if tp1_hit and not tp2_hit:
            hit = high >= tp2 if is_long else low <= tp2
            if hit:
                _reduce(tp2, SCALE[1], "take_profit_2")
                tp2_hit = True
                continue
        if tp2_hit and not tp3_hit:
            hit = high >= tp3 if is_long else low <= tp3
            if hit:
                _reduce(tp3, None, "take_profit_3", True)
                tp3_hit = True
                break

    if rem > 0:
        direction = 1.0 if is_long else -1.0
        fee = abs(last_close * rem) * (fee_pct / 100.0)
        realized += (last_close - entry) * rem * direction - fee
        fees += fee
        exit_reason = "time_exit"
        rem = 0.0

    r_mult = realized / (risk_dist * qty0) if risk_dist * qty0 > 1e-12 else 0.0
    return {
        "pnl": round(realized, 4),
        "fees": round(fees, 4),
        "exit": exit_reason,
        "r": round(r_mult, 4),
        "bars": bars,
        "tp1": tp1_hit,
        "tp2": tp2_hit,
        "tp3": tp3_hit,
        "stop": stop,
        "entry": entry,
        "qty": qty0,
    }


def _mae_mfe_atr(
    *,
    is_long: bool,
    entry: float,
    stop: float,
    atr: float,
    candles: list[Candle],
    entry_at: datetime,
    exit_at: datetime | None,
) -> dict[str, float]:
    entry_at = ensure_utc(entry_at)
    exit_at = ensure_utc(exit_at) if exit_at else None
    mae = mfe = 0.0
    for c in candles:
        when = ensure_utc(c.open_time)
        if when < entry_at:
            continue
        if exit_at and when > exit_at:
            break
        if is_long:
            mfe = max(mfe, float(c.high) - entry)
            mae = max(mae, entry - float(c.low))
        else:
            mfe = max(mfe, entry - float(c.low))
            mae = max(mae, float(c.high) - entry)
    r = abs(entry - stop)
    atr = atr if atr > 0 else r
    return {
        "mae_atr": round(mae / atr, 3),
        "mfe_atr": round(mfe / atr, 3),
        "mae_r": round(mae / r, 3) if r > 0 else 0.0,
        "mfe_r": round(mfe / r, 3) if r > 0 else 0.0,
        "stop_atr": round(r / atr, 3) if atr > 0 else 0.0,
    }


async def _load_trades() -> list[TradeRow]:
    async with session_scope() as session:
        acct = (
            await session.execute(select(PaperAccount).where(PaperAccount.name == "default"))
        ).scalar_one()
        positions = (
            await session.execute(
                select(PaperPosition).where(
                    PaperPosition.account_id == acct.id,
                    PaperPosition.status.in_(("closed", "pending", "cancelled")),
                )
            )
        ).scalars().all()
        sig_ids = [p.signal_id for p in positions if p.signal_id]
        signals: dict[Any, Signal] = {}
        if sig_ids:
            rows = (
                await session.execute(select(Signal).where(Signal.id.in_(sig_ids)))
            ).scalars().all()
            signals = {s.id: s for s in rows}

    out: list[TradeRow] = []
    for p in positions:
        notes = _parse_notes(p.notes)
        sig = signals.get(p.signal_id) if p.signal_id else None
        armed = notes["armed_at"]
        if armed is None and sig is not None:
            armed = ensure_utc(sig.created_at)
        out.append(
            TradeRow(
                id=int(p.id),
                symbol=str(p.symbol).upper(),
                direction=str(p.direction),
                status=str(p.status),
                entry=float(p.entry_price) if p.entry_price is not None else None,
                stop=float(p.stop_loss) if p.stop_loss is not None else None,
                qty=float(p.initial_quantity or 0),
                risk_usd=float(p.risk_amount or 0) or 50.0,
                opened_at=ensure_utc(p.opened_at) if p.opened_at else None,
                closed_at=ensure_utc(p.closed_at) if p.closed_at else None,
                exit_reason=str(p.exit_reason) if p.exit_reason else None,
                actual_pnl=float(p.realized_pnl or 0),
                score=float(p.signal_score) if p.signal_score is not None else None,
                timeframe=str(p.timeframe or (sig.primary_timeframe if sig else "1h") or "1h"),
                notes=str(p.notes or ""),
                signal_id=p.signal_id,
                ref_entry=notes["ref"],
                orig_sl=notes["orig_sl"],
                atr_note=notes["atr"],
                armed_at=armed,
                signal_created=ensure_utc(sig.created_at) if sig else None,
                signal_entry_low=float(sig.entry_low) if sig and sig.entry_low is not None else None,
                signal_entry_high=float(sig.entry_high) if sig and sig.entry_high is not None else None,
                signal_stop=float(sig.stop_loss) if sig and sig.stop_loss is not None else None,
                signal_ref=float(sig.reference_price) if sig and sig.reference_price is not None else None,
            )
        )
    return out


async def _load_candle_map(
    trades: list[TradeRow],
) -> dict[tuple[str, str], list[Candle]]:
    settings = get_settings()
    needed: dict[tuple[str, str], tuple[datetime, datetime]] = {}
    now = utc_now()
    for t in trades:
        key = (t.symbol, t.timeframe)
        start_candidates = [x for x in (t.armed_at, t.signal_created, t.opened_at) if x]
        start = min(start_candidates) if start_candidates else now - timedelta(days=14)
        start = start - timeframe_to_timedelta(t.timeframe) * 40
        end = t.closed_at or now
        if key not in needed:
            needed[key] = (start, end)
        else:
            a, b = needed[key]
            needed[key] = (min(a, start), max(b, end))

    cache: dict[tuple[str, str], list[Candle]] = {}
    async with session_scope() as session:
        repo = AssetRepository(session)
        for i, ((symbol, tf), (start, end)) in enumerate(needed.items(), start=1):
            series = await repo.load_candle_series(
                symbol, tf, start_time=start, end_time=end, limit=100_000
            )
            if series is None or series.is_empty:
                cache[(symbol, tf)] = []
            else:
                df = series.to_dataframe()
                if "open_time" in df.columns:
                    df["open_time"] = __import__("pandas").to_datetime(df["open_time"], utc=True)
                    df = df.set_index("open_time", drop=False)
                cache[(symbol, tf)] = _candles_from_df(df, tf)
            if i % 25 == 0 or i == len(needed):
                print(f"  candles {i}/{len(needed)}", file=sys.stderr, flush=True)
    return cache


def _resolve_atr(t: TradeRow, candles: list[Candle]) -> float | None:
    if t.atr_note and t.atr_note > 0:
        return float(t.atr_note)
    when = t.armed_at or t.signal_created or t.opened_at
    if when is None or not candles:
        return None
    idx = _idx_at(candles, when)
    if idx is None:
        return None
    return wilder_atr(candles, idx)


def _reference(t: TradeRow) -> float | None:
    if t.ref_entry and t.ref_entry > 0:
        return t.ref_entry
    if _dir_long(t.direction) and t.signal_entry_low:
        return t.signal_entry_low
    if (not _dir_long(t.direction)) and t.signal_entry_high:
        return t.signal_entry_high
    if t.signal_ref:
        return t.signal_ref
    return t.entry


def _orig_stop(t: TradeRow) -> float | None:
    if t.orig_sl and t.orig_sl > 0:
        return t.orig_sl
    if t.signal_stop and t.signal_stop > 0:
        return t.signal_stop
    return t.stop


def _analyze_closed(trades: list[TradeRow], candles: dict[tuple[str, str], list[Candle]]) -> dict[str, Any]:
    rows = []
    exits: dict[str, int] = defaultdict(int)
    stop_outs = 0
    for t in trades:
        if t.status != "closed" or t.entry is None or t.stop is None or t.opened_at is None:
            continue
        cs = candles.get((t.symbol, t.timeframe)) or []
        atr = _resolve_atr(t, cs)
        exits[str(t.exit_reason or "unknown")] += 1
        if str(t.exit_reason or "").lower() in ("stop_loss", "sl", "stopped"):
            stop_outs += 1
        path = (
            _mae_mfe_atr(
                is_long=_dir_long(t.direction),
                entry=t.entry,
                stop=t.stop,
                atr=atr or abs(t.entry - t.stop),
                candles=cs,
                entry_at=t.opened_at,
                exit_at=t.closed_at,
            )
            if cs
            else {}
        )
        rows.append(
            {
                "id": t.id,
                "symbol": t.symbol,
                "direction": t.direction,
                "pnl": round(t.actual_pnl, 2),
                "exit": t.exit_reason,
                "score": t.score,
                "atr": round(atr, 8) if atr else None,
                "stop_dist_pct": round(abs(t.entry - t.stop) / t.entry * 100, 3),
                **path,
            }
        )
    n = len(rows)
    stop_atr = [r["stop_atr"] for r in rows if r.get("stop_atr")]
    mae_before_sl = [
        r
        for r in rows
        if str(r.get("exit") or "").lower() in ("stop_loss", "sl", "stopped")
        and float(r.get("mfe_r") or 0) >= 0.5
    ]
    return {
        "n": n,
        "stop_out_n": stop_outs,
        "stop_out_pct": round(stop_outs / n * 100, 1) if n else 0.0,
        "median_stop_atr": round(sorted(stop_atr)[len(stop_atr) // 2], 3) if stop_atr else None,
        "mean_stop_atr": round(sum(stop_atr) / len(stop_atr), 3) if stop_atr else None,
        "stopped_with_mfe_ge_0.5R": len(mae_before_sl),
        "stopped_with_mfe_ge_0.5R_pct": round(len(mae_before_sl) / stop_outs * 100, 1)
        if stop_outs
        else 0.0,
        "exits": dict(exits),
        "actual_net": round(sum(r["pnl"] for r in rows), 2),
        "actual_wr": round(sum(1 for r in rows if r["pnl"] > 0) / n * 100, 1) if n else 0.0,
        "sample": sorted(rows, key=lambda r: float(r["pnl"]))[:8]
        + sorted(rows, key=lambda r: float(r["pnl"]), reverse=True)[:5],
    }


def _sim_closed_setting(
    trades: list[TradeRow],
    candles: dict[tuple[str, str], list[Candle]],
    *,
    cfg: dict[str, Any],
    fee_pct: float,
    move_be: bool,
) -> dict[str, Any]:
    results = []
    skipped = 0
    for t in trades:
        if t.status != "closed" or t.entry is None or t.opened_at is None:
            continue
        cs = candles.get((t.symbol, t.timeframe)) or []
        atr = _resolve_atr(t, cs)
        if not atr or atr <= 0 or not cs:
            skipped += 1
            continue
        is_long = _dir_long(t.direction)
        stop = _new_stop(t.entry, atr, is_long=is_long, sl_atr=float(cfg["sl_atr"]))
        # Keep same $ risk as paper
        risk = t.risk_usd if t.risk_usd > 0 else abs(t.entry - (t.stop or stop)) * t.qty
        qty = _qty_for_risk(t.entry, stop, risk)
        # Cap at actual close time so we don't invent post-exit PnL.
        end = t.closed_at or (t.opened_at + timedelta(hours=48))
        sim = _replay(
            is_long=is_long,
            entry=t.entry,
            stop=stop,
            qty=qty,
            candles=cs,
            entry_at=t.opened_at,
            fee_pct=fee_pct,
            move_be=move_be,
            end_at=end,
        )
        results.append(
            {
                "id": t.id,
                "symbol": t.symbol,
                "actual_pnl": round(t.actual_pnl, 2),
                "sim_pnl": sim["pnl"],
                "delta": round(sim["pnl"] - t.actual_pnl, 2),
                "exit": sim["exit"],
                "actual_exit": t.exit_reason,
                "r": sim["r"],
                "stop_atr": round(abs(t.entry - stop) / atr, 3),
            }
        )
    n = len(results)
    net = sum(r["sim_pnl"] for r in results)
    wins = sum(1 for r in results if r["sim_pnl"] > 0)
    sl_n = sum(1 for r in results if r["exit"] == "stop_loss")
    gp = sum(r["sim_pnl"] for r in results if r["sim_pnl"] > 0)
    gl = abs(sum(r["sim_pnl"] for r in results if r["sim_pnl"] < 0))
    return {
        "key": cfg["key"],
        "label": cfg["label"],
        "bucket": "closed",
        "n": n,
        "skipped_no_atr": skipped,
        "net_pnl": round(net, 2),
        "delta_vs_actual": round(net - sum(r["actual_pnl"] for r in results), 2),
        "win_rate": round(wins / n * 100, 1) if n else 0.0,
        "profit_factor": round(gp / gl, 3) if gl > 0 else (99.0 if gp > 0 else 0.0),
        "stop_out_pct": round(sl_n / n * 100, 1) if n else 0.0,
        "total_r": round(sum(r["r"] for r in results), 2),
        "expectancy_r": round(sum(r["r"] for r in results) / n, 3) if n else 0.0,
        "best_deltas": sorted(results, key=lambda r: r["delta"], reverse=True)[:5],
        "worst_deltas": sorted(results, key=lambda r: r["delta"])[:5],
    }


def _is_retest_arm(t: TradeRow) -> bool:
    notes = (t.notes or "").lower()
    return (
        t.status == "pending"
        or "retest_pending" in notes
        or "retest_skipped" in notes
        or "retest_cancelled" in notes
        or "pending_expired" in notes
        or "skipped_expiry" in notes
        or "zone=" in notes
    )


def _sim_pending_setting(
    trades: list[TradeRow],
    candles: dict[tuple[str, str], list[Candle]],
    *,
    cfg: dict[str, Any],
    fee_pct: float,
    move_be: bool,
    pending_mult: int,
) -> dict[str, Any]:
    """Open pending + historical retest-skips: would new zone/SL have filled?"""
    filled_rows = []
    still_pending = 0
    skipped = 0
    expired = 0
    sl_before = 0
    other_skip = 0
    open_pending = 0
    hist_skip = 0
    for t in trades:
        if t.status == "closed":
            continue
        if t.status == "pending":
            open_pending += 1
        elif t.status == "cancelled" and _is_retest_arm(t):
            hist_skip += 1
        else:
            continue
        cs = candles.get((t.symbol, t.timeframe)) or []
        atr = _resolve_atr(t, cs)
        ref = _reference(t)
        arm_time = t.armed_at or t.signal_created
        if not atr or not ref or not arm_time or not cs:
            skipped += 1
            continue
        is_long = _dir_long(t.direction)
        orig_sl = _new_stop(ref, atr, is_long=is_long, sl_atr=float(cfg["sl_atr"]))
        arm = arm_retest_entry(
            direction=SignalDirection.LONG if is_long else SignalDirection.SHORT,
            arm_time=arm_time,
            reference_entry=ref,
            original_stop=orig_sl,
            timeframe=t.timeframe,
            candles=cs,
            config=RetestEntryConfig(
                zone_near=Decimal(str(cfg["zone_near"])),
                zone_far=Decimal(str(cfg["zone_far"])),
                pending_multiplier=pending_mult,
                min_bars_in_zone=1,
                trendline_gate_enabled=False,
            ),
        )
        if arm.status == "pending":
            still_pending += 1
            continue
        if not arm.filled:
            if arm.status == "skipped_expiry":
                expired += 1
            elif arm.status == "skipped_sl":
                sl_before += 1
            else:
                other_skip += 1
            continue
        assert arm.fill_price is not None and arm.fill_time is not None and arm.stop is not None
        risk = t.risk_usd if t.risk_usd > 0 else 50.0
        qty = _qty_for_risk(float(arm.fill_price), float(arm.stop), risk)
        # Historical skips: manage until ~48h after fill; open pending: until now
        if t.status == "pending":
            end = utc_now()
        else:
            end = ensure_utc(arm.fill_time) + timedelta(hours=48)
        sim = _replay(
            is_long=is_long,
            entry=float(arm.fill_price),
            stop=float(arm.stop),
            qty=qty,
            candles=cs,
            entry_at=ensure_utc(arm.fill_time),
            fee_pct=fee_pct,
            move_be=move_be,
            end_at=end,
        )
        filled_rows.append(
            {
                "id": t.id,
                "symbol": t.symbol,
                "direction": t.direction,
                "source": "open_pending" if t.status == "pending" else "hist_skip",
                "score": t.score,
                "fill": round(float(arm.fill_price), 8),
                "stop": round(float(arm.stop), 8),
                "sim_pnl": sim["pnl"],
                "exit": sim["exit"],
                "r": sim["r"],
            }
        )

    n_pool = open_pending + hist_skip
    n_fill = len(filled_rows)
    net = sum(r["sim_pnl"] for r in filled_rows)
    wins = sum(1 for r in filled_rows if r["sim_pnl"] > 0)
    gp = sum(r["sim_pnl"] for r in filled_rows if r["sim_pnl"] > 0)
    gl = abs(sum(r["sim_pnl"] for r in filled_rows if r["sim_pnl"] < 0))
    open_fills = sum(1 for r in filled_rows if r["source"] == "open_pending")
    hist_fills = sum(1 for r in filled_rows if r["source"] == "hist_skip")
    return {
        "key": cfg["key"],
        "label": cfg["label"],
        "bucket": "pending",
        "open_pending": open_pending,
        "hist_retest_skips": hist_skip,
        "pool_total": n_pool,
        "would_fill": n_fill,
        "would_fill_open": open_fills,
        "would_fill_hist_skip": hist_fills,
        "fill_rate_pct": round(n_fill / n_pool * 100, 1) if n_pool else 0.0,
        "still_pending": still_pending,
        "expired_no_fill": expired,
        "sl_before_fill": sl_before,
        "other_skip": other_skip,
        "skipped_no_data": skipped,
        "net_pnl_if_filled": round(net, 2),
        "win_rate_filled": round(wins / n_fill * 100, 1) if n_fill else 0.0,
        "profit_factor_filled": round(gp / gl, 3) if gl > 0 else (99.0 if gp > 0 else 0.0),
        "total_r_filled": round(sum(r["r"] for r in filled_rows), 2),
        "fills": sorted(filled_rows, key=lambda r: r["sim_pnl"], reverse=True)[:12],
        "fills_worst": sorted(filled_rows, key=lambda r: r["sim_pnl"])[:8],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=str, default="exports/sl_atr_variants.json")
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    fee = float(settings.paper_fee_percent)
    move_be = bool(settings.paper_move_stop_to_breakeven)
    pending_mult = int(settings.paper_retest_pending_multiplier)

    print("Loading paper closed + pending...", file=sys.stderr, flush=True)
    trades = await _load_trades()
    closed_n = sum(1 for t in trades if t.status == "closed")
    pending_n = sum(1 for t in trades if t.status == "pending")
    cancel_n = sum(1 for t in trades if t.status == "cancelled" and _is_retest_arm(t))
    print(
        f"  closed={closed_n} open_pending={pending_n} hist_retest_skips={cancel_n}",
        file=sys.stderr,
        flush=True,
    )

    print("Loading candles...", file=sys.stderr, flush=True)
    candles = await _load_candle_map(trades)

    autopsy = _analyze_closed(trades, candles)
    print(
        f"Autopsy: stop_out={autopsy['stop_out_pct']}% median_stop_atr={autopsy['median_stop_atr']} "
        f"stopped_w_MFE≥0.5R={autopsy['stopped_with_mfe_ge_0.5R_pct']}%",
        file=sys.stderr,
        flush=True,
    )

    closed_results = []
    pending_results = []
    for cfg in SETTINGS:
        print(f"Sim {cfg['key']}...", file=sys.stderr, flush=True)
        closed_results.append(
            _sim_closed_setting(trades, candles, cfg=cfg, fee_pct=fee, move_be=move_be)
        )
        pending_results.append(
            _sim_pending_setting(
                trades,
                candles,
                cfg=cfg,
                fee_pct=fee,
                move_be=move_be,
                pending_mult=pending_mult,
            )
        )

    closed_ranked = sorted(closed_results, key=lambda r: r["net_pnl"], reverse=True)
    pending_ranked = sorted(
        pending_results, key=lambda r: (r["net_pnl_if_filled"], r["would_fill"]), reverse=True
    )

    # Combined: closed sim + pending opportunity PnL
    combined = []
    for c in closed_results:
        p = next(x for x in pending_results if x["key"] == c["key"])
        combined.append(
            {
                "key": c["key"],
                "label": c["label"],
                "closed_net": c["net_pnl"],
                "pending_net_if_filled": p["net_pnl_if_filled"],
                "pending_fills": p["would_fill"],
                "combined_net": round(c["net_pnl"] + p["net_pnl_if_filled"], 2),
                "closed_wr": c["win_rate"],
                "closed_stop_out_pct": c["stop_out_pct"],
                "pending_fill_rate_pct": p["fill_rate_pct"],
            }
        )
    combined_ranked = sorted(combined, key=lambda r: r["combined_net"], reverse=True)

    payload = {
        "generated_at": utc_now().isoformat(),
        "label": "sl_atr_variants_closed_pending",
        "config": {
            "fee_percent": fee,
            "tp_multipliers": list(TP_MULTS),
            "scale_out": list(SCALE),
            "move_be_after_tp1": move_be,
            "pending_multiplier": pending_mult,
            "trendline_gate": False,
            "settings": SETTINGS,
            "note": (
                "Closed: same actual entry, stop rebuilt as entry±ATR×sl_atr, qty resized "
                "to keep paper risk_usd. Pending: re-arm retest with new zone + SL from ref; "
                "PnL only if fill occurs (opportunity, not currently booked)."
            ),
        },
        "counts": {
            "closed": closed_n,
            "open_pending": pending_n,
            "hist_retest_skips": cancel_n,
        },
        "autopsy_closed": autopsy,
        "closed_ranking": closed_ranked,
        "pending_ranking": pending_ranked,
        "combined_ranking": combined_ranked,
        "closed_results": closed_results,
        "pending_results": pending_results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"wrote": str(out), "combined_ranking": combined_ranked}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
