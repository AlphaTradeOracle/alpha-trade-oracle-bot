"""Equity: live paper ledger vs combo settings (SL 1.8 / zone 0.40–1.15).

Builds two cumulative equity curves from $initial:
  - live: actual closed realized_pnl by closed_at (+ live MTM endpoint)
  - combo: closed trades re-stopped at 1.8×ATR (paper risk sizing) + hist
    retest-skips that would fill under zone 0.40–1.15

    python scripts/compare_paper_equity_combo.py --out /tmp/paper_equity_combo.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
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
from app.market_data.types import Candle
from app.models.paper import PaperAccount, PaperPosition
from app.models.signal import Signal
from app.repositories.asset_repository import AssetRepository
from app.signals.retest_entry import (
    RetestEntryConfig,
    arm_retest_entry,
    levels_from_entry_sl,
    wilder_atr,
)
from app.signals.risk import RiskManager

_ATR_NOTE = re.compile(r"(?:^|;)atr=(?P<v>[0-9.eE+-]+)")
_REF_NOTE = re.compile(r"ref_entry=(?P<v>[0-9.eE+-]+)")
_ORIG_SL = re.compile(r"orig_sl=(?P<v>[0-9.eE+-]+)")
_ARMED = re.compile(r"armed_at=(?P<v>[^;]+)")

SL_ATR = 1.8
ZONE_NEAR = 0.40
ZONE_FAR = 1.15
SCALE = (0.5, 0.25, 0.25)
TP_MULTS = (1.5, 2.5, 4.0)


@dataclass
class Pos:
    id: int
    symbol: str
    direction: str
    status: str
    entry: float | None
    stop: float | None
    opened_at: datetime | None
    closed_at: datetime | None
    actual_pnl: float
    timeframe: str
    notes: str
    atr_note: float | None
    ref: float | None
    orig_sl: float | None
    armed_at: datetime | None
    signal_created: datetime | None
    signal_entry_low: float | None
    signal_entry_high: float | None
    signal_stop: float | None
    signal_ref: float | None


def _parse_notes(notes: str | None) -> dict[str, Any]:
    text = notes or ""
    out: dict[str, Any] = {"atr": None, "ref": None, "orig_sl": None, "armed_at": None}
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
            out["armed_at"] = datetime.fromisoformat(m.group("v").strip().replace("Z", "+00:00"))
        except Exception:
            pass
    return out


def _long(d: str) -> bool:
    return "LONG" in d.upper()


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


def _size(
    entry: float,
    stop: float,
    *,
    risk_usd: float,
    leverage: float,
    max_notional: float,
    margin_per_trade: float,
) -> float:
    """Match PaperTradingService._size_position."""
    dist = abs(entry - stop)
    if entry <= 0 or dist <= 0:
        return 0.0
    if risk_usd <= 0:
        # Fixed-margin mode (live default when paper_risk_per_trade_usd=0)
        notional = margin_per_trade * leverage
        return notional / entry
    qty = RiskManager.position_size_for_risk(risk_usd, dist)
    notional = qty * entry
    if max_notional > 0 and notional > max_notional:
        qty = max_notional / entry
    return float(qty)


def _replay(
    *,
    is_long: bool,
    entry: float,
    stop: float,
    qty: float,
    candles: list[Candle],
    entry_at: datetime,
    end_at: datetime,
    fee_pct: float,
    move_be: bool,
) -> dict[str, Any]:
    if qty <= 0:
        return {"pnl": 0.0, "exit": "no_size", "exit_at": entry_at, "r": 0.0}
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
    tp1_hit = tp2_hit = False
    exit_reason = "open"
    exit_at = ensure_utc(entry_at)
    last_close = entry
    entry_at = ensure_utc(entry_at)
    end_at = ensure_utc(end_at)

    def _reduce(price: float, frac: float | None, reason: str, when: datetime, all_rest: bool = False) -> None:
        nonlocal rem, realized, fees, exit_reason, exit_at
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
        exit_at = when
        if rem < 1e-12:
            rem = 0.0

    for c in candles:
        when = ensure_utc(c.open_time)
        if when < entry_at:
            continue
        if when > end_at:
            break
        if rem <= 0:
            break
        high, low = float(c.high), float(c.low)
        last_close = float(c.close)
        if (low <= cur_stop) if is_long else (high >= cur_stop):
            _reduce(cur_stop, None, "stop_loss", when, True)
            break
        if not tp1_hit:
            if (high >= tp1) if is_long else (low <= tp1):
                _reduce(tp1, SCALE[0], "take_profit_1", when)
                tp1_hit = True
                if move_be:
                    cur_stop = entry
                continue
        if tp1_hit and not tp2_hit:
            if (high >= tp2) if is_long else (low <= tp2):
                _reduce(tp2, SCALE[1], "take_profit_2", when)
                tp2_hit = True
                continue
        if tp2_hit:
            if (high >= tp3) if is_long else (low <= tp3):
                _reduce(tp3, None, "take_profit_3", when, True)
                break

    if rem > 0:
        direction = 1.0 if is_long else -1.0
        fee = abs(last_close * rem) * (fee_pct / 100.0)
        realized += (last_close - entry) * rem * direction - fee
        exit_reason = "time_exit"
        exit_at = end_at

    r = realized / (risk_dist * qty0) if risk_dist * qty0 > 1e-12 else 0.0
    return {
        "pnl": round(realized, 4),
        "exit": exit_reason,
        "exit_at": exit_at,
        "r": round(r, 4),
        "entry": entry,
        "stop": stop,
    }


def _curve(events: list[tuple[datetime, float]], *, start: float, start_at: datetime, end_eq: float, end_at: datetime) -> list[dict[str, Any]]:
    pts = [{"t": start_at.isoformat(), "equity": round(start, 2)}]
    eq = start
    for when, pnl in sorted(events, key=lambda x: x[0]):
        eq += pnl
        t = when.isoformat()
        if pts and pts[-1]["t"] == t:
            pts[-1]["equity"] = round(eq, 2)
        else:
            pts.append({"t": t, "equity": round(eq, 2)})
    if not pts or pts[-1]["t"] != end_at.isoformat():
        pts.append({"t": end_at.isoformat(), "equity": round(end_eq, 2)})
    else:
        pts[-1]["equity"] = round(end_eq, 2)
    return pts


def _daily(curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, float] = {}
    for p in curve:
        by_day[p["t"][:10]] = p["equity"]
    return [{"date": d, "equity": by_day[d]} for d in sorted(by_day)]


def _side(direction: str) -> str:
    return "LONG" if _long(direction) else "SHORT"


_FORCE_SOURCES = frozenset(
    {"closed_replay", "closed_fallback", "open_occupancy"}
)


def _apply_caps(
    trades: list[dict[str, Any]],
    *,
    start_equity: float,
    start_at: datetime,
    end_at: datetime,
    max_open: int,
    max_per_direction: int,
) -> dict[str, Any]:
    # Chronological. Live skeleton always kept; skip fills only if a slot is free at fill.
    ordered = sorted(
        trades,
        key=lambda t: (
            t["entry_at"],
            int(t.get("priority", 1)),
            t["symbol"],
            t["id"],
        ),
    )
    open_book: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    occ_at_skip: list[int] = []
    for trade in ordered:
        entry = trade["entry_at"]
        open_book = [o for o in open_book if o["exit_at"] > entry]
        dir_count = sum(1 for o in open_book if o["side"] == trade["side"])
        force = trade.get("source") in _FORCE_SOURCES
        if trade.get("source") == "skip_fill":
            occ_at_skip.append(len(open_book))
        if not force:
            if len(open_book) >= max_open:
                skipped.append({**trade, "skip_reason": "max_open"})
                continue
            if dir_count >= max_per_direction:
                skipped.append({**trade, "skip_reason": "max_per_direction"})
                continue
        open_book.append(trade)
        accepted.append(trade)

    # Equity events ignore pure occupancy blockers (open positions, pnl=0).
    events = [
        (t["exit_at"], float(t["net_pnl"]))
        for t in accepted
        if t.get("source") != "open_occupancy"
    ]
    end_eq = start_equity + sum(p for _, p in events)
    curve = _curve(events, start=start_equity, start_at=start_at, end_eq=end_eq, end_at=end_at)
    skip_reasons = {
        "max_open": sum(1 for s in skipped if s.get("skip_reason") == "max_open"),
        "max_per_direction": sum(
            1 for s in skipped if s.get("skip_reason") == "max_per_direction"
        ),
    }
    by_src = {
        "closed_replay": sum(1 for t in accepted if t.get("source") == "closed_replay"),
        "skip_fill": sum(1 for t in accepted if t.get("source") == "skip_fill"),
        "closed_fallback": sum(1 for t in accepted if t.get("source") == "closed_fallback"),
        "open_occupancy": sum(1 for t in accepted if t.get("source") == "open_occupancy"),
    }
    occ_sorted = sorted(occ_at_skip)
    def _pct(p: float) -> int | None:
        if not occ_sorted:
            return None
        return occ_sorted[min(len(occ_sorted) - 1, int(round((len(occ_sorted) - 1) * p)))]

    return {
        "accepted_n": sum(1 for t in accepted if t.get("source") != "open_occupancy"),
        "skipped_n": len(skipped),
        "skip_reasons": skip_reasons,
        "accepted_by_source": by_src,
        "net_pnl": round(sum(float(t["net_pnl"]) for t in accepted), 2),
        "closed_replay_net": round(
            sum(
                float(t["net_pnl"])
                for t in accepted
                if t.get("source") in ("closed_replay", "closed_fallback")
            ),
            2,
        ),
        "skip_fills_accepted": by_src["skip_fill"],
        "skip_fills_net": round(
            sum(float(t["net_pnl"]) for t in accepted if t.get("source") == "skip_fill"),
            2,
        ),
        "end_equity": round(end_eq, 2),
        "return_pct": round((end_eq / start_equity - 1) * 100, 2) if start_equity else 0.0,
        "curve": curve,
        "daily": _daily(curve),
        "occupancy_at_skip_fill": {
            "n": len(occ_at_skip),
            "min": occ_sorted[0] if occ_sorted else None,
            "p50": _pct(0.5),
            "p90": _pct(0.9),
            "max": occ_sorted[-1] if occ_sorted else None,
            "share_at_or_above_20": round(
                sum(1 for x in occ_at_skip if x >= 20) / len(occ_at_skip), 3
            )
            if occ_at_skip
            else None,
        },
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="/tmp/paper_equity_combo.json")
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    fee = float(settings.paper_fee_percent)
    move_be = bool(settings.paper_move_stop_to_breakeven)
    risk_usd = float(settings.paper_risk_per_trade_usd)
    leverage = float(settings.paper_leverage)
    max_notional = float(settings.paper_max_notional_usd)
    margin_per_trade = float(settings.paper_margin_per_trade)
    pending_mult = int(settings.paper_retest_pending_multiplier)
    max_open = int(settings.paper_max_open_positions)
    max_per_dir = int(settings.paper_max_open_per_direction)

    async with session_scope() as session:
        acct = (
            await session.execute(select(PaperAccount).where(PaperAccount.name == "default"))
        ).scalar_one()
        initial = float(acct.initial_balance)
        cash = float(acct.cash_balance)
        realized = float(acct.realized_pnl)
        positions = (
            await session.execute(
                select(PaperPosition).where(
                    PaperPosition.account_id == acct.id,
                    PaperPosition.status.in_(("closed", "pending", "cancelled", "open")),
                )
            )
        ).scalars().all()
        sig_ids = [p.signal_id for p in positions if p.signal_id]
        signals: dict[Any, Signal] = {}
        if sig_ids:
            rows = (await session.execute(select(Signal).where(Signal.id.in_(sig_ids)))).scalars().all()
            signals = {s.id: s for s in rows}

    open_margin = sum(
        float(p.margin_used or 0) for p in positions if str(p.status) == "open"
    )
    # Desk mark: cash + margin on open positions (same as portfolio snapshot without live marks).
    live_equity = cash + open_margin
    closed_realized = sum(
        float(p.realized_pnl or 0) for p in positions if str(p.status) == "closed"
    )
    open_upnl = live_equity - (initial + closed_realized)

    rows: list[Pos] = []
    for p in positions:
        notes = _parse_notes(p.notes)
        sig = signals.get(p.signal_id) if p.signal_id else None
        armed = notes["armed_at"] or (ensure_utc(sig.created_at) if sig else None)
        rows.append(
            Pos(
                id=int(p.id),
                symbol=str(p.symbol).upper(),
                direction=str(p.direction),
                status=str(p.status),
                entry=float(p.entry_price) if p.entry_price is not None else None,
                stop=float(p.stop_loss) if p.stop_loss is not None else None,
                opened_at=ensure_utc(p.opened_at) if p.opened_at else None,
                closed_at=ensure_utc(p.closed_at) if p.closed_at else None,
                actual_pnl=float(p.realized_pnl or 0),
                timeframe=str(p.timeframe or (sig.primary_timeframe if sig else "1h") or "1h"),
                notes=str(p.notes or ""),
                atr_note=notes["atr"],
                ref=notes["ref"],
                orig_sl=notes["orig_sl"],
                armed_at=armed,
                signal_created=ensure_utc(sig.created_at) if sig else None,
                signal_entry_low=float(sig.entry_low) if sig and sig.entry_low is not None else None,
                signal_entry_high=float(sig.entry_high) if sig and sig.entry_high is not None else None,
                signal_stop=float(sig.stop_loss) if sig and sig.stop_loss is not None else None,
                signal_ref=float(sig.reference_price) if sig and sig.reference_price is not None else None,
            )
        )

    closed = [r for r in rows if r.status == "closed" and r.closed_at and r.entry]
    opens = [r for r in rows if r.status == "open" and r.opened_at and r.entry]
    skips = [
        r
        for r in rows
        if r.status == "cancelled"
        and (
            "retest" in (r.notes or "").lower()
            or "zone=" in (r.notes or "")
            or r.ref is not None
        )
    ]
    print(
        f"live_equity={live_equity:.2f} closed={len(closed)} skips={len(skips)} "
        f"risk_usd={risk_usd} margin={margin_per_trade} lev={leverage}",
        file=sys.stderr,
        flush=True,
    )

    # Live curve from actual closes
    start_at = min((c.opened_at or c.closed_at for c in closed if c.opened_at or c.closed_at), default=utc_now())
    live_events = [(c.closed_at, c.actual_pnl) for c in closed if c.closed_at]
    now = utc_now()
    live_curve = _curve(live_events, start=initial, start_at=start_at, end_eq=live_equity, end_at=now)

    # Candles
    needed: dict[tuple[str, str], tuple[datetime, datetime]] = {}
    for r in closed + skips:
        key = (r.symbol, r.timeframe)
        start = min(x for x in (r.armed_at, r.signal_created, r.opened_at) if x) if any(
            [r.armed_at, r.signal_created, r.opened_at]
        ) else now - timedelta(days=14)
        start = start - timeframe_to_timedelta(r.timeframe) * 40
        end = r.closed_at or now
        if key not in needed:
            needed[key] = (start, end)
        else:
            a, b = needed[key]
            needed[key] = (min(a, start), max(b, end))

    cache: dict[tuple[str, str], list[Candle]] = {}
    print(f"Loading {len(needed)} candle series...", file=sys.stderr, flush=True)
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
                    import pandas as pd

                    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
                    df = df.set_index("open_time", drop=False)
                cache[(symbol, tf)] = _candles_from_df(df, tf)
            if i % 40 == 0 or i == len(needed):
                print(f"  {i}/{len(needed)}", file=sys.stderr, flush=True)

    combo_trades: list[dict[str, Any]] = []
    closed_sims = []
    skip_fills = []

    for c in closed:
        cs = cache.get((c.symbol, c.timeframe)) or []
        atr = c.atr_note
        when = c.opened_at or c.armed_at
        if (not atr or atr <= 0) and when and cs:
            idx = _idx_at(cs, when)
            if idx is not None:
                atr = wilder_atr(cs, idx)
        if not atr or not c.entry or not c.opened_at or not cs:
            if c.closed_at and c.opened_at:
                combo_trades.append(
                    {
                        "id": c.id,
                        "symbol": c.symbol,
                        "side": _side(c.direction),
                        "source": "closed_fallback",
                        "entry_at": c.opened_at,
                        "exit_at": c.closed_at,
                        "net_pnl": c.actual_pnl,
                        "r": 0.0,
                        "exit": "actual",
                        "priority": 0,
                    }
                )
            continue
        is_long = _long(c.direction)
        stop = c.entry - atr * SL_ATR if is_long else c.entry + atr * SL_ATR
        qty = _size(
            c.entry,
            stop,
            risk_usd=risk_usd,
            leverage=leverage,
            max_notional=max_notional,
            margin_per_trade=margin_per_trade,
        )
        sim = _replay(
            is_long=is_long,
            entry=c.entry,
            stop=stop,
            qty=qty,
            candles=cs,
            entry_at=c.opened_at,
            end_at=c.closed_at or (c.opened_at + timedelta(hours=48)),
            fee_pct=fee,
            move_be=move_be,
        )
        # Occupancy uses live open/close so book throughput matches reality;
        # PnL uses combo re-stop replay.
        combo_trades.append(
            {
                "id": c.id,
                "symbol": c.symbol,
                "side": _side(c.direction),
                "source": "closed_replay",
                "entry_at": c.opened_at,
                "exit_at": c.closed_at or sim["exit_at"],
                "net_pnl": sim["pnl"],
                "r": sim["r"],
                "exit": sim["exit"],
                "priority": 0,
            }
        )
        closed_sims.append(
            {
                "id": c.id,
                "symbol": c.symbol,
                "actual": round(c.actual_pnl, 2),
                "combo": sim["pnl"],
                "delta": round(sim["pnl"] - c.actual_pnl, 2),
                "exit": sim["exit"],
                "r": sim["r"],
            }
        )

    for s in skips:
        cs = cache.get((s.symbol, s.timeframe)) or []
        atr = s.atr_note
        arm_time = s.armed_at or s.signal_created
        ref = s.ref
        if ref is None:
            if _long(s.direction) and s.signal_entry_low:
                ref = s.signal_entry_low
            elif (not _long(s.direction)) and s.signal_entry_high:
                ref = s.signal_entry_high
            else:
                ref = s.signal_ref
        if (not atr or atr <= 0) and arm_time and cs:
            idx = _idx_at(cs, arm_time)
            if idx is not None:
                atr = wilder_atr(cs, idx)
        if not atr or not ref or not arm_time or not cs:
            continue
        is_long = _long(s.direction)
        orig_sl = ref - atr * SL_ATR if is_long else ref + atr * SL_ATR
        arm = arm_retest_entry(
            direction=SignalDirection.LONG if is_long else SignalDirection.SHORT,
            arm_time=arm_time,
            reference_entry=ref,
            original_stop=orig_sl,
            timeframe=s.timeframe,
            candles=cs,
            config=RetestEntryConfig(
                zone_near=Decimal(str(ZONE_NEAR)),
                zone_far=Decimal(str(ZONE_FAR)),
                pending_multiplier=pending_mult,
                min_bars_in_zone=1,
                trendline_gate_enabled=False,
            ),
        )
        if not arm.filled or arm.fill_price is None or arm.fill_time is None or arm.stop is None:
            continue
        qty = _size(
            float(arm.fill_price),
            float(arm.stop),
            risk_usd=risk_usd,
            leverage=leverage,
            max_notional=max_notional,
            margin_per_trade=margin_per_trade,
        )
        sim = _replay(
            is_long=is_long,
            entry=float(arm.fill_price),
            stop=float(arm.stop),
            qty=qty,
            candles=cs,
            entry_at=ensure_utc(arm.fill_time),
            end_at=ensure_utc(arm.fill_time) + timedelta(hours=48),
            fee_pct=fee,
            move_be=move_be,
        )
        combo_trades.append(
            {
                "id": s.id,
                "symbol": s.symbol,
                "side": _side(s.direction),
                "source": "skip_fill",
                "entry_at": ensure_utc(arm.fill_time),
                "exit_at": sim["exit_at"],
                "net_pnl": sim["pnl"],
                "r": sim["r"],
                "exit": sim["exit"],
                "priority": 1,
            }
        )
        skip_fills.append(
            {
                "id": s.id,
                "symbol": s.symbol,
                "pnl": sim["pnl"],
                "exit": sim["exit"],
                "r": sim["r"],
                "exit_at": sim["exit_at"].isoformat(),
            }
        )

    # Currently open positions occupy slots (no PnL event — already in live mark).
    for o in opens:
        combo_trades.append(
            {
                "id": o.id,
                "symbol": o.symbol,
                "side": _side(o.direction),
                "source": "open_occupancy",
                "entry_at": o.opened_at,
                "exit_at": now + timedelta(days=365),
                "net_pnl": 0.0,
                "r": 0.0,
                "exit": "open",
                "priority": 0,
            }
        )

    combo_uncapped = _apply_caps(
        combo_trades,
        start_equity=initial,
        start_at=start_at,
        end_at=now,
        max_open=10_000,
        max_per_direction=10_000,
    )
    combo_capped = _apply_caps(
        combo_trades,
        start_equity=initial,
        start_at=start_at,
        end_at=now,
        max_open=max_open,
        max_per_direction=max_per_dir,
    )

    # Cap sensitivity: how many skip fills fit if we raise max_open.
    cap_sensitivity: list[dict[str, Any]] = []
    for mo in (20, 24, 30, 40, 60):
        run = _apply_caps(
            combo_trades,
            start_equity=initial,
            start_at=start_at,
            end_at=now,
            max_open=mo,
            max_per_direction=max(max_per_dir, mo),  # don't let dir-cap dominate sweep
        )
        cap_sensitivity.append(
            {
                "max_open": mo,
                "max_per_direction": max(max_per_dir, mo),
                "end_equity": run["end_equity"],
                "return_pct": run["return_pct"],
                "skip_fills_accepted": run["skip_fills_accepted"],
                "skip_fills_net": run["skip_fills_net"],
                "skipped_n": run["skipped_n"],
                "skip_reasons": run["skip_reasons"],
            }
        )

    live_daily = _daily(live_curve)
    days = sorted(
        {d["date"] for d in live_daily}
        | {d["date"] for d in combo_uncapped["daily"]}
        | {d["date"] for d in combo_capped["daily"]}
    )
    live_map = {d["date"]: d["equity"] for d in live_daily}
    unc_map = {d["date"]: d["equity"] for d in combo_uncapped["daily"]}
    cap_map = {d["date"]: d["equity"] for d in combo_capped["daily"]}
    aligned = []
    lv = cvu = cvc = initial
    for d in days:
        if d in live_map:
            lv = live_map[d]
        if d in unc_map:
            cvu = unc_map[d]
        if d in cap_map:
            cvc = cap_map[d]
        aligned.append(
            {
                "date": d,
                "live": round(lv, 2),
                "combo_uncapped": round(cvu, 2),
                "combo_capped": round(cvc, 2),
            }
        )

    payload = {
        "generated_at": now.isoformat(),
        "label": "paper_equity_live_vs_combo_capped",
        "settings_combo": {
            "atr_multiplier": SL_ATR,
            "zone_near": ZONE_NEAR,
            "zone_far": ZONE_FAR,
            "risk_per_trade_usd": risk_usd,
            "margin_per_trade": margin_per_trade,
            "leverage": leverage,
            "fee_percent": fee,
            "paper_max_open_positions": max_open,
            "paper_max_open_per_direction": max_per_dir,
        },
        "live": {
            "initial": initial,
            "end_equity": round(live_equity, 2),
            "return_pct": round((live_equity / initial - 1) * 100, 2),
            "closed_n": len(closed),
            "closed_net": round(sum(c.actual_pnl for c in closed), 2),
            "open_upnl_implied": round(open_upnl, 2),
            "cash": round(cash, 2),
            "open_margin": round(open_margin, 2),
            "curve": live_curve,
            "daily": live_daily,
        },
        "combo_uncapped": {
            **{k: v for k, v in combo_uncapped.items() if k not in ("curve",)},
            "curve": combo_uncapped["curve"],
            "candidate_trades": len(combo_trades),
            "skip_fills_candidates": len(skip_fills),
        },
        "combo_capped": {
            **{k: v for k, v in combo_capped.items() if k not in ("curve",)},
            "curve": combo_capped["curve"],
            "candidate_trades": len(combo_trades),
            "skip_fills_candidates": len(skip_fills),
        },
        "delta_capped_vs_live": {
            "end_equity": round(combo_capped["end_equity"] - live_equity, 2),
            "return_pp": round(
                (combo_capped["end_equity"] / initial - live_equity / initial) * 100, 2
            ),
        },
        "delta_uncapped_vs_live": {
            "end_equity": round(combo_uncapped["end_equity"] - live_equity, 2),
            "return_pp": round(
                (combo_uncapped["end_equity"] / initial - live_equity / initial) * 100, 2
            ),
        },
        "aligned_daily": aligned,
        "cap_sensitivity": cap_sensitivity,
        "occupancy_at_skip_fill": combo_capped.get("occupancy_at_skip_fill"),
        "closed_sample_best": sorted(closed_sims, key=lambda x: x["delta"], reverse=True)[:8],
        "closed_sample_worst": sorted(closed_sims, key=lambda x: x["delta"])[:8],
        "skip_sample_best": sorted(skip_fills, key=lambda x: x["pnl"], reverse=True)[:8],
        "skip_sample_worst": sorted(skip_fills, key=lambda x: x["pnl"])[:8],
        "note": (
            "Live = actual paper closed PnL path + cash+margin end. "
            "Combo = closed re-stop 1.8×ATR + hist skip fills under zone 0.40–1.15. "
            "Capped = max_open / max_per_direction like live paper (checked at fill). "
            "Live closed/open skeleton always reserved; skip fills only into free slots. "
            "Uncapped = no book limits (upper bound)."
        ),
    }
    # Keep legacy keys for older consumers
    payload["combo"] = payload["combo_uncapped"]
    payload["delta"] = payload["delta_uncapped_vs_live"]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "wrote": str(out),
                "live_end": payload["live"]["end_equity"],
                "combo_uncapped": payload["combo_uncapped"]["end_equity"],
                "combo_capped": payload["combo_capped"]["end_equity"],
                "delta_capped": payload["delta_capped_vs_live"],
                "capped_skips_accepted": payload["combo_capped"]["skip_fills_accepted"],
                "capped_skipped_by_caps": payload["combo_capped"]["skipped_n"],
                "skip_reasons": payload["combo_capped"]["skip_reasons"],
                "occupancy_at_skip_fill": payload.get("occupancy_at_skip_fill"),
                "cap_sensitivity": payload.get("cap_sensitivity"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
