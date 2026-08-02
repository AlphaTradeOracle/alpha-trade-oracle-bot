#!/usr/bin/env python3
"""Counterfactual: how would paper retest arms have done with IST (immediate) entry?

For each paper position armed as retest (filled or skipped), simulate:
  IST: enter at reference_entry on the first primary bar open after arm_time,
       stop = original signal stop, TPs = pure R multiples from settings,
       manage on OHLC until expiry (signal_expiry_multiplier × TF from entry).

Compares to actual paper outcome (retest fill / skip / closed PnL).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.container import build_container
from app.core.enums import ExitReason, SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, timeframe_to_timedelta, utc_now
from app.database.session import session_scope
from app.models.paper import PaperPosition
from app.models.signal import Signal
from app.signals.retest_entry import levels_from_entry_sl
from app.signals.risk import tp_multipliers_from_settings

_ZONE = re.compile(r"zone=(?P<lo>[0-9.eE+-]+)-(?P<hi>[0-9.eE+-]+)")
_REF = re.compile(r"ref_entry=(?P<v>[0-9.eE+-]+)")
_ORIG_SL = re.compile(r"orig_sl=(?P<v>[0-9.eE+-]+)")
_ARMED = re.compile(r"armed_at=(?P<v>[^;]+)")


@dataclass
class SimResult:
    symbol: str
    direction: str
    actual_status: str
    actual_exit: str | None
    actual_pnl: float
    ist_entry: float
    ist_stop: float
    ist_exit_reason: str
    ist_pnl: float
    ist_r: float
    delta_pnl: float  # ist - actual (cancelled actual=0)
    note: str


def _parse_notes(notes: str | None) -> dict[str, float | str | None]:
    text = notes or ""
    out: dict[str, float | str | None] = {
        "ref": None,
        "orig_sl": None,
        "armed_at": None,
        "zone_lo": None,
        "zone_hi": None,
    }
    m = _REF.search(text)
    if m:
        out["ref"] = float(m.group("v"))
    m = _ORIG_SL.search(text)
    if m:
        out["orig_sl"] = float(m.group("v"))
    m = _ARMED.search(text)
    if m:
        out["armed_at"] = m.group("v").strip()
    m = _ZONE.search(text)
    if m:
        out["zone_lo"] = float(m.group("lo"))
        out["zone_hi"] = float(m.group("hi"))
    return out


def _simulate_ist(
    *,
    direction: SignalDirection,
    entry: float,
    stop: float,
    candles: list,
    entry_at: datetime,
    expires_at: datetime,
    fee_pct: float,
    risk_usd: float,
    tp_mults: tuple[float, float, float],
    scale: tuple[float, float, float],
    move_be: bool,
) -> tuple[str, float, float]:
    """Return (exit_reason, net_pnl, r_multiple) for IST path."""
    is_long = direction.is_long
    stop_dist = abs(entry - stop)
    if stop_dist <= 0 or entry <= 0:
        return "invalid", 0.0, 0.0

    qty = risk_usd / stop_dist
    fee_rate = fee_pct / 100.0
    entry_fee = entry * qty * fee_rate

    tp1, tp2, tp3 = levels_from_entry_sl(
        Decimal(str(entry)),
        Decimal(str(stop)),
        is_long=is_long,
        multipliers=tuple(Decimal(str(m)) for m in tp_mults),
    )
    tps = [float(tp1), float(tp2), float(tp3)]
    fractions = list(scale)
    remaining = qty
    initial = qty
    current_stop = stop
    filled = [False, False, False]
    realized = -entry_fee
    direction_sign = 1.0 if is_long else -1.0

    bars = [
        c
        for c in candles
        if ensure_utc(c.open_time) >= entry_at
    ]
    if not bars:
        return "no_bars", realized, realized / risk_usd

    for candle in bars:
        when = ensure_utc(candle.open_time)
        high = float(candle.high)
        low = float(candle.low)
        close = float(candle.close)

        stop_hit = low <= current_stop if is_long else high >= current_stop
        if stop_hit:
            px = current_stop
            gross = (px - entry) * remaining * direction_sign
            fee = px * remaining * fee_rate
            realized += gross - fee
            return ExitReason.STOP_LOSS.value, realized, realized / risk_usd

        for level, (tp, frac) in enumerate(zip(tps, fractions), start=1):
            if filled[level - 1]:
                continue
            hit = high >= tp if is_long else low <= tp
            if not hit:
                break
            slice_qty = remaining if level == 3 else min(initial * frac, remaining)
            gross = (tp - entry) * slice_qty * direction_sign
            fee = tp * slice_qty * fee_rate
            realized += gross - fee
            remaining -= slice_qty
            filled[level - 1] = True
            if level == 1 and move_be:
                # Fee-aware BE (entry ± 2×fee) — inline so script runs without redeploy.
                rate = max(fee_pct, 0.0) / 100.0
                current_stop = (
                    entry * (1.0 + 2.0 * rate) if is_long else entry * (1.0 - 2.0 * rate)
                )
            if remaining <= 1e-12:
                reason = (
                    ExitReason.TAKE_PROFIT_3.value
                    if level == 3
                    else ExitReason.TAKE_PROFIT_1.value
                    if level == 1
                    else ExitReason.TAKE_PROFIT_2.value
                )
                return reason, realized, realized / risk_usd

        if when >= expires_at and remaining > 1e-12:
            gross = (close - entry) * remaining * direction_sign
            fee = close * remaining * fee_rate
            realized += gross - fee
            return ExitReason.EXPIRED.value, realized, realized / risk_usd

    # end of data
    last = bars[-1]
    close = float(last.close)
    if remaining > 1e-12:
        gross = (close - entry) * remaining * direction_sign
        fee = close * remaining * fee_rate
        realized += gross - fee
        return ExitReason.END_OF_DATA.value, realized, realized / risk_usd
    return "flat", realized, realized / risk_usd


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-07-31T16:32:35+00:00")
    parser.add_argument("--out", default="exports/retest_vs_ist.json")
    args = parser.parse_args()
    since = ensure_utc(datetime.fromisoformat(args.since))

    container = build_container()
    settings = container.settings
    configure_logging(settings.log_level, json_output=False)
    provider = container.provider
    tp_mults = tp_multipliers_from_settings(settings)
    scale = tuple(settings.parsed_scale_out_fractions)
    fee_pct = float(settings.paper_fee_percent)
    risk_usd = float(settings.paper_risk_per_trade_usd)
    expiry_mult = int(settings.signal_expiry_multiplier)
    lookback_pad = timedelta(days=3)

    rows: list[SimResult] = []

    async with session_scope() as session:
        result = await session.execute(
            select(PaperPosition)
            .where(PaperPosition.opened_at >= since)
            .order_by(PaperPosition.opened_at)
        )
        positions = list(result.scalars())
        signal_ids = [p.signal_id for p in positions if p.signal_id is not None]
        signals: dict[int, Signal] = {}
        if signal_ids:
            sig_rows = await session.execute(select(Signal).where(Signal.id.in_(signal_ids)))
            signals = {s.id: s for s in sig_rows.scalars()}

    for pos in positions:
        notes = pos.notes or ""
        if "retest" not in notes and pos.exit_reason != "retest_skipped":
            continue

        parsed = _parse_notes(notes)
        direction = SignalDirection(pos.direction)
        tf = pos.timeframe or "1h"
        sig = signals.get(pos.signal_id) if pos.signal_id else None

        # Prefer signal geometry for true IST counterfactual
        ref = parsed["ref"]
        orig_sl = parsed["orig_sl"]
        if sig is not None:
            if ref is None:
                lo = float(sig.entry_low or sig.reference_price or 0)
                hi = float(sig.entry_high or sig.reference_price or 0)
                ref = (lo + hi) / 2.0 if lo and hi else float(sig.reference_price or 0)
            if orig_sl is None and sig.stop_loss is not None:
                orig_sl = float(sig.stop_loss)
        if ref is None:
            ref = float(pos.entry_price)
        if orig_sl is None:
            orig_sl = float(pos.stop_loss)

        armed_raw = parsed["armed_at"]
        if armed_raw:
            try:
                arm_time = ensure_utc(datetime.fromisoformat(armed_raw.replace("Z", "+00:00")))
            except ValueError:
                arm_time = ensure_utc(pos.opened_at)
        elif sig is not None and sig.created_at is not None:
            arm_time = ensure_utc(sig.created_at)
        else:
            arm_time = ensure_utc(pos.opened_at)

        # IST entry: first bar open at/after arm_time
        start = arm_time - lookback_pad
        end = utc_now() + timedelta(hours=1)
        try:
            series = await provider.get_candles(
                pos.symbol.upper(),
                tf,
                start_time=start,
                end_time=end,
                limit=500,
            )
        except Exception as exc:
            rows.append(
                SimResult(
                    symbol=pos.symbol,
                    direction=pos.direction,
                    actual_status=pos.status,
                    actual_exit=pos.exit_reason,
                    actual_pnl=float(pos.realized_pnl or 0),
                    ist_entry=float(ref),
                    ist_stop=float(orig_sl),
                    ist_exit_reason=f"candle_error:{exc}",
                    ist_pnl=0.0,
                    ist_r=0.0,
                    delta_pnl=0.0,
                    note="fetch_failed",
                )
            )
            continue

        candles = list(series.candles)
        entry_candle = next(
            (c for c in candles if ensure_utc(c.open_time) >= arm_time),
            None,
        )
        if entry_candle is None:
            rows.append(
                SimResult(
                    symbol=pos.symbol,
                    direction=pos.direction,
                    actual_status=pos.status,
                    actual_exit=pos.exit_reason,
                    actual_pnl=float(pos.realized_pnl or 0),
                    ist_entry=float(ref),
                    ist_stop=float(orig_sl),
                    ist_exit_reason="no_entry_bar",
                    ist_pnl=0.0,
                    ist_r=0.0,
                    delta_pnl=-float(pos.realized_pnl or 0),
                    note="no_bar",
                )
            )
            continue

        ist_entry = float(entry_candle.open)
        # Keep original signal stop (not re-anchored) — true IST from signal geometry
        ist_stop = float(orig_sl)
        # If stop is on wrong side of IST open (slippage past stop), mark skipped
        if direction.is_long and ist_stop >= ist_entry:
            ist_stop = ist_entry * 0.99
        if (not direction.is_long) and ist_stop <= ist_entry:
            ist_stop = ist_entry * 1.01

        entry_at = ensure_utc(entry_candle.open_time)
        expires_at = entry_at + expiry_mult * timeframe_to_timedelta(tf)

        reason, pnl, r_mult = _simulate_ist(
            direction=direction,
            entry=ist_entry,
            stop=ist_stop,
            candles=candles,
            entry_at=entry_at,
            expires_at=expires_at,
            fee_pct=fee_pct,
            risk_usd=risk_usd,
            tp_mults=tp_mults,
            scale=scale,
            move_be=bool(settings.paper_move_stop_to_breakeven),
        )

        actual_pnl = float(pos.realized_pnl or 0)
        if pos.status == "cancelled":
            actual_pnl = 0.0  # no capital deployed

        rows.append(
            SimResult(
                symbol=pos.symbol,
                direction=pos.direction,
                actual_status=pos.status,
                actual_exit=pos.exit_reason,
                actual_pnl=actual_pnl,
                ist_entry=ist_entry,
                ist_stop=ist_stop,
                ist_exit_reason=reason,
                ist_pnl=round(pnl, 2),
                ist_r=round(r_mult, 2),
                delta_pnl=round(pnl - actual_pnl, 2),
                note=f"arm={arm_time.isoformat()} ref={ref} orig_sl={orig_sl}",
            )
        )

    await container.aclose()

    # Summaries
    filled_like = [r for r in rows if r.actual_status in ("closed", "open")]
    skipped = [r for r in rows if r.actual_status == "cancelled"]
    all_ist = sum(r.ist_pnl for r in rows)
    actual_total = sum(r.actual_pnl for r in rows)
    filled_ist = sum(r.ist_pnl for r in filled_like)
    filled_actual = sum(r.actual_pnl for r in filled_like)
    skipped_ist = sum(r.ist_pnl for r in skipped)

    payload = {
        "generated_at": utc_now().isoformat(),
        "since": since.isoformat(),
        "assumptions": {
            "ist_entry": "first primary bar open at/after arm_time",
            "ist_stop": "original signal stop from notes (orig_sl)",
            "tps": list(tp_mults),
            "scale_out": list(scale),
            "risk_usd": risk_usd,
            "fee_percent": fee_pct,
            "cancelled_actual_pnl": 0.0,
        },
        "summary": {
            "n_positions": len(rows),
            "n_filled_or_open": len(filled_like),
            "n_cancelled": len(skipped),
            "actual_pnl_total": round(actual_total, 2),
            "ist_pnl_total": round(all_ist, 2),
            "delta_total": round(all_ist - actual_total, 2),
            "filled_actual_pnl": round(filled_actual, 2),
            "filled_ist_pnl": round(filled_ist, 2),
            "skipped_ist_pnl_if_traded": round(skipped_ist, 2),
            "ist_win_rate": round(
                sum(1 for r in rows if r.ist_pnl > 0) / len(rows), 3
            )
            if rows
            else 0.0,
            "ist_stop_outs": sum(1 for r in rows if r.ist_exit_reason == "stop_loss"),
            "ist_tp_exits": sum(
                1 for r in rows if "take_profit" in (r.ist_exit_reason or "")
            ),
        },
        "rows": [asdict(r) for r in rows],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    s = payload["summary"]
    print(
        f"positions={s['n_positions']} filled/open={s['n_filled_or_open']} "
        f"cancelled={s['n_cancelled']}"
    )
    print(
        f"ACTUAL total PnL ${s['actual_pnl_total']:+.2f}  |  "
        f"IST total PnL ${s['ist_pnl_total']:+.2f}  |  "
        f"delta ${s['delta_total']:+.2f}"
    )
    print(
        f"  among filled/open: actual ${s['filled_actual_pnl']:+.2f} → "
        f"IST ${s['filled_ist_pnl']:+.2f}"
    )
    print(
        f"  among cancelled (0 actual): IST-if-traded ${s['skipped_ist_pnl_if_traded']:+.2f}"
    )
    print(
        f"IST WR {s['ist_win_rate']*100:.1f}%  stops={s['ist_stop_outs']}  "
        f"tp_exits={s['ist_tp_exits']}"
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
