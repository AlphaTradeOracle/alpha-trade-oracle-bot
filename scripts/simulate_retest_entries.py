"""Counterfactual pending-retest entry simulation against all paper positions.

Baseline: fill at historical paper entry, then SL / TP scale-out / BE / expiry.
Retest: arm at signal time; fill only if price revisits the ATR pullback zone
before original SL or pending expiry (4× TF). Live strategy is NOT changed.

Outputs a single JSON object to stdout.
"""

from __future__ import annotations

import asyncio
import json
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
from app.repositories.paper_repository import PaperRepository
from app.signals.retest_entry import zone_fill_price

FEE = Decimal("0.001")
SCALE = (Decimal("0.33333333"), Decimal("0.33333333"), Decimal("0.33333334"))
MOVE_STOP_TO_BE = True
MARGIN = Decimal("100")
LEVERAGE = Decimal("10")
PENDING_MULT = 4
ZONE_NEAR = Decimal("0.35")  # reference − 0.35·ATR (long upper edge)
ZONE_FAR = Decimal("1.0")  # reference − 1.0·ATR (long lower edge)
ATR_PERIOD = 14
TP_MULTIPLIERS = (Decimal("2"), Decimal("4"), Decimal("6"))

TF_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
}


@dataclass
class TradeInput:
    id: int
    symbol: str
    direction: str
    status: str
    timeframe: str
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    qty: float
    notional: float
    opened_at: datetime
    expires_at: datetime | None
    closed_at: datetime | None
    actual_pnl: float
    actual_fees: float
    actual_exit: str | None
    signal_created_at: datetime | None = None


@dataclass
class ArmResult:
    status: str  # filled | skipped_sl | skipped_expiry | skipped_no_candles | skipped_no_atr
    fill_price: float | None = None
    fill_time: datetime | None = None
    zone_low: float | None = None
    zone_high: float | None = None
    atr: float | None = None
    bars_waited: int = 0
    note: str = ""


@dataclass
class ReplayResult:
    arm: str
    pnl: float
    fees: float
    exit_reason: str
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    bars: int = 0
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    filled: bool = True
    note: str = ""


def _tf_delta(tf: str) -> timedelta:
    return timedelta(seconds=TF_SECONDS.get(tf, 3600))


def _wilder_atr(candles: list[Candle], end_idx: int, period: int = ATR_PERIOD) -> float | None:
    """ATR at candle end_idx using bars up to and including end_idx."""
    if end_idx < period:
        return None
    trs: list[float] = []
    for i in range(1, end_idx + 1):
        h = float(candles[i].high)
        l = float(candles[i].low)
        prev_c = float(candles[i - 1].close)
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr if atr > 0 else None


def _retest_zone(reference: Decimal, atr: Decimal, is_long: bool) -> tuple[Decimal, Decimal]:
    near = atr * ZONE_NEAR
    far = atr * ZONE_FAR
    if is_long:
        # Pullback below breakout: [ref−1.0ATR, ref−0.35ATR]
        lo = reference - far
        hi = reference - near
        return lo, hi
    lo = reference + near
    hi = reference + far
    return lo, hi


def _levels_from_entry_sl(entry: Decimal, stop: Decimal, is_long: bool) -> tuple[Decimal, Decimal, Decimal]:
    risk = abs(entry - stop)
    if risk <= 0:
        risk = entry * Decimal("0.01")
    direction = Decimal("1") if is_long else Decimal("-1")
    tps = tuple(entry + direction * m * risk for m in TP_MULTIPLIERS)
    return tps[0], tps[1], tps[2]


def _arm_retest(trade: TradeInput, candles: list[Candle]) -> ArmResult:
    is_long = SignalDirection(trade.direction).is_long
    arm_time = ensure_utc(trade.signal_created_at or trade.opened_at)
    reference = Decimal(str(trade.entry))
    stop = Decimal(str(trade.stop_loss))
    pending_until = arm_time + PENDING_MULT * _tf_delta(trade.timeframe)

    # Find signal bar index (last bar with open_time <= arm_time)
    sig_idx = None
    for i, c in enumerate(candles):
        if ensure_utc(c.open_time) <= arm_time:
            sig_idx = i
        else:
            break
    if sig_idx is None:
        return ArmResult(status="skipped_no_candles", note="no_bar_at_or_before_signal")

    atr_f = _wilder_atr(candles, sig_idx)
    if atr_f is None:
        return ArmResult(status="skipped_no_atr", note="insufficient_history_for_atr")
    atr = Decimal(str(atr_f))
    zone_lo, zone_hi = _retest_zone(reference, atr, is_long)

    bars_waited = 0
    for c in candles[sig_idx + 1 :]:
        when = ensure_utc(c.open_time)
        if when > pending_until:
            return ArmResult(
                status="skipped_expiry",
                zone_low=float(zone_lo),
                zone_high=float(zone_hi),
                atr=float(atr),
                bars_waited=bars_waited,
                note="pending_expired_no_fill",
            )
        bars_waited += 1
        high = Decimal(str(float(c.high)))
        low = Decimal(str(float(c.low)))

        # Invalidate if original SL touched before fill
        if is_long and low <= stop:
            return ArmResult(
                status="skipped_sl",
                zone_low=float(zone_lo),
                zone_high=float(zone_hi),
                atr=float(atr),
                bars_waited=bars_waited,
                note="sl_before_retest",
            )
        if (not is_long) and high >= stop:
            return ArmResult(
                status="skipped_sl",
                zone_low=float(zone_lo),
                zone_high=float(zone_hi),
                atr=float(atr),
                bars_waited=bars_waited,
                note="sl_before_retest",
            )

        # Zone touch
        touched = (low <= zone_hi and high >= zone_lo) if is_long else (low <= zone_hi and high >= zone_lo)
        if touched:
            fill = zone_fill_price(
                low=low, high=high, zone_lo=zone_lo, zone_hi=zone_hi, is_long=is_long
            )
            return ArmResult(
                status="filled",
                fill_price=float(fill),
                fill_time=when,
                zone_low=float(zone_lo),
                zone_high=float(zone_hi),
                atr=float(atr),
                bars_waited=bars_waited,
            )

    return ArmResult(
        status="skipped_expiry",
        zone_low=float(zone_lo),
        zone_high=float(zone_hi),
        atr=float(atr),
        bars_waited=bars_waited,
        note="data_ended_before_fill",
    )


def _replay_from_fill(
    *,
    arm: str,
    direction: str,
    entry: Decimal,
    stop: Decimal,
    tp1: Decimal,
    tp2: Decimal,
    tp3: Decimal,
    fill_time: datetime,
    candles: list[Candle],
    expiry_at: datetime | None,
) -> ReplayResult:
    is_long = SignalDirection(direction).is_long
    notional = MARGIN * LEVERAGE
    qty0 = notional / entry if entry > 0 else Decimal("0")
    rem = qty0
    realized = Decimal("0")
    fees = Decimal("0")
    fees += notional * FEE
    realized -= notional * FEE

    current_stop = stop
    tp1_hit = tp2_hit = tp3_hit = False
    exit_reason = "open"
    bars = 0
    closed = False
    note = ""

    def reduce(price: Decimal, fraction: Decimal | None, reason: str, *, all_rest: bool = False) -> None:
        nonlocal rem, realized, fees, exit_reason, closed
        if rem <= 0:
            return
        qty = rem if all_rest or fraction is None else min(qty0 * fraction, rem)
        if qty <= 0:
            return
        direction_s = Decimal("1") if is_long else Decimal("-1")
        gross = (price - entry) * qty * direction_s
        fee = price * qty * FEE
        rem -= qty
        realized += gross - fee
        fees += fee
        exit_reason = reason
        if rem <= Decimal("0.00000001"):
            rem = Decimal("0")
            closed = True

    for c in candles:
        if rem <= 0:
            break
        when = ensure_utc(c.open_time)
        if when < ensure_utc(fill_time):
            continue
        bars += 1
        high = Decimal(str(float(c.high)))
        low = Decimal(str(float(c.low)))
        close = Decimal(str(float(c.close)))

        stop_hit = low <= current_stop if is_long else high >= current_stop
        if stop_hit:
            reduce(current_stop, None, "stop_loss", all_rest=True)
            break

        fav = high if is_long else low
        if not tp1_hit:
            hit = fav >= tp1 if is_long else fav <= tp1
            if hit:
                reduce(tp1, SCALE[0], "take_profit_1")
                tp1_hit = True
                if MOVE_STOP_TO_BE:
                    current_stop = entry
        if tp1_hit and not tp2_hit and rem > 0:
            hit = fav >= tp2 if is_long else fav <= tp2
            if hit:
                reduce(tp2, SCALE[1], "take_profit_2")
                tp2_hit = True
        if tp2_hit and not tp3_hit and rem > 0:
            hit = fav >= tp3 if is_long else fav <= tp3
            if hit:
                reduce(tp3, None, "take_profit_3", all_rest=True)
                tp3_hit = True
                break

        if rem <= 0:
            break

        if expiry_at is not None and when >= ensure_utc(expiry_at) and rem > 0:
            reduce(close, None, "expired", all_rest=True)
            break

    if rem > 0:
        last = Decimal(str(float(candles[-1].close))) if candles else entry
        reduce(last, None, "data_end_mtm", all_rest=True)
        note = "marked_to_market_at_last_candle"
        closed = False

    return ReplayResult(
        arm=arm,
        pnl=round(float(realized), 4),
        fees=round(float(fees), 4),
        exit_reason=exit_reason,
        entry=float(entry),
        stop_loss=float(stop),
        tp1=float(tp1),
        tp2=float(tp2),
        tp3=float(tp3),
        bars=bars,
        tp1_hit=tp1_hit,
        tp2_hit=tp2_hit,
        tp3_hit=tp3_hit,
        filled=True,
        note=note,
    )


def _agg(rows: list[ReplayResult], *, count_skips_as_zero: bool = False) -> dict[str, Any]:
    pnls = [r.pnl for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = sum(losses)
    filled = [r for r in rows if r.filled]
    return {
        "n": len(rows),
        "filled": len(filled),
        "skipped": len(rows) - len(filled),
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl_all": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "avg_pnl_filled": round(sum(r.pnl for r in filled) / len(filled), 2) if filled else 0.0,
        "wins": len(wins),
        "losses": len([p for p in pnls if p < 0]),
        "flats": sum(1 for p in pnls if p == 0),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
        "win_rate_filled": round(
            len([r for r in filled if r.pnl > 0]) / len(filled), 4
        )
        if filled
        else 0.0,
        "profit_factor": round(gross_win / gross_loss, 4)
        if gross_loss > 0
        else (99.0 if gross_win > 0 else 0.0),
        "exit_counts": _count_exits(rows),
        "tp1_hits": sum(1 for r in rows if r.tp1_hit),
        "tp2_hits": sum(1 for r in rows if r.tp2_hit),
        "tp3_hits": sum(1 for r in rows if r.tp3_hit),
        "mtm_open": sum(1 for r in rows if r.exit_reason == "data_end_mtm"),
        "count_skips_as_zero": count_skips_as_zero,
    }


def _count_exits(rows: list[ReplayResult]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r.exit_reason] = out.get(r.exit_reason, 0) + 1
    return out


async def _load_candles_db(
    session,
    symbol: str,
    timeframe: str,
    start: datetime,
) -> list[Candle]:
    asset = (
        await session.execute(select(Asset).where(Asset.symbol == symbol.upper()))
    ).scalar_one_or_none()
    if asset is None:
        return []
    start_utc = ensure_utc(start)
    result = await session.execute(
        select(MarketCandle)
        .where(
            MarketCandle.asset_id == asset.id,
            MarketCandle.timeframe == timeframe,
            MarketCandle.is_closed.is_(True),
            MarketCandle.open_time >= start_utc,
        )
        .order_by(MarketCandle.open_time.asc())
        .limit(100_000)
    )
    rows = list(result.scalars())
    interval = timeframe_to_timedelta(timeframe)
    return [
        Candle(
            open_time=ensure_utc(row.open_time),
            close_time=ensure_utc(row.close_time)
            if row.close_time is not None
            else ensure_utc(row.open_time) + interval,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            quote_volume=float(row.quote_volume) if row.quote_volume is not None else None,
            trade_count=row.trade_count,
            is_closed=bool(row.is_closed),
        )
        for row in rows
    ]


async def _load_candles(
    session,
    provider,
    symbol: str,
    timeframe: str,
    start: datetime,
) -> tuple[list[Candle], str]:
    # Need ATR history before signal
    hist_start = ensure_utc(start) - timedelta(days=5)
    db_candles = await _load_candles_db(session, symbol, timeframe, hist_start)
    if len(db_candles) >= ATR_PERIOD + 5:
        return db_candles, "db"

    try:
        live = await provider.get_candles(
            symbol,
            timeframe,
            limit=100_000,
            start_time=hist_start,
            end_time=utc_now(),
        )
        return list(live.candles), "exchange"
    except Exception as exc:  # noqa: BLE001
        print(f"  candle miss {symbol} {timeframe}: {exc}", file=sys.stderr)
        return db_candles, "db_sparse"


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    trades: list[TradeInput] = []
    async with session_scope() as session:
        account = await container.paper_trading.get_or_create_account(session)
        repo = PaperRepository(session)
        positions = await repo.list_positions(account.id)

        signal_ids = [p.signal_id for p in positions if p.signal_id]
        signal_map: dict[int, datetime] = {}
        if signal_ids:
            result = await session.execute(
                select(Signal.id, Signal.created_at).where(Signal.id.in_(signal_ids))
            )
            signal_map = {int(i): ensure_utc(t) for i, t in result.all()}

        for p in positions:
            trades.append(
                TradeInput(
                    id=int(p.id),
                    symbol=p.symbol,
                    direction=p.direction,
                    status=p.status,
                    timeframe=p.timeframe or "1h",
                    entry=float(p.entry_price),
                    stop_loss=float(p.stop_loss),
                    tp1=float(p.take_profit_1),
                    tp2=float(p.take_profit_2),
                    tp3=float(p.take_profit_3),
                    qty=float(p.initial_quantity),
                    notional=float(p.notional),
                    opened_at=ensure_utc(p.opened_at),
                    expires_at=ensure_utc(p.expires_at) if p.expires_at else None,
                    closed_at=ensure_utc(p.closed_at) if p.closed_at else None,
                    actual_pnl=float(p.realized_pnl),
                    actual_fees=float(p.fees),
                    actual_exit=p.exit_reason,
                    signal_created_at=signal_map.get(int(p.signal_id)) if p.signal_id else None,
                )
            )

    print(f"Loaded {len(trades)} paper positions", file=sys.stderr)

    candle_cache: dict[tuple[str, str], tuple[list[Candle], str]] = {}
    baseline_rows: list[ReplayResult] = []
    retest_rows: list[ReplayResult] = []
    details: list[dict[str, Any]] = []

    try:
        async with session_scope() as session:
            for t in trades:
                key = (t.symbol.upper(), t.timeframe)
                if key not in candle_cache:
                    candles, src = await _load_candles(
                        session, container.provider, t.symbol, t.timeframe, t.opened_at
                    )
                    candle_cache[key] = (candles, src)
                    print(
                        f"  candles {key[0]} {key[1]}: {len(candles)} ({src})",
                        file=sys.stderr,
                    )

                candles, src = candle_cache[key]
                is_long = SignalDirection(t.direction).is_long
                entry = Decimal(str(t.entry))
                stop = Decimal(str(t.stop_loss))
                tp1 = Decimal(str(t.tp1))
                tp2 = Decimal(str(t.tp2))
                tp3 = Decimal(str(t.tp3))
                fill_time = ensure_utc(t.opened_at)
                # Expiry from fill for baseline (recorded expires or 4× from open)
                if t.expires_at is not None:
                    baseline_expiry = ensure_utc(t.expires_at)
                else:
                    baseline_expiry = fill_time + PENDING_MULT * _tf_delta(t.timeframe)

                usable_after = [c for c in candles if ensure_utc(c.open_time) >= fill_time]
                row: dict[str, Any] = {
                    "id": t.id,
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "status": t.status,
                    "timeframe": t.timeframe,
                    "opened_at": t.opened_at.isoformat(),
                    "actual_pnl": round(t.actual_pnl, 4),
                    "actual_exit": t.actual_exit,
                    "paper_entry": t.entry,
                    "candle_source": src,
                    "candle_count": len(candles),
                }

                if len(usable_after) < 1:
                    row["skipped"] = "no_candles_after_entry"
                    details.append(row)
                    continue

                baseline = _replay_from_fill(
                    arm="baseline",
                    direction=t.direction,
                    entry=entry,
                    stop=stop,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3,
                    fill_time=fill_time,
                    candles=candles,
                    expiry_at=baseline_expiry,
                )
                baseline_rows.append(baseline)
                row["baseline"] = asdict(baseline)

                arm = _arm_retest(t, candles)
                row["retest_arm"] = asdict(arm)

                if arm.status != "filled" or arm.fill_price is None or arm.fill_time is None:
                    skip = ReplayResult(
                        arm="retest",
                        pnl=0.0,
                        fees=0.0,
                        exit_reason=arm.status,
                        entry=t.entry,
                        stop_loss=t.stop_loss,
                        tp1=t.tp1,
                        tp2=t.tp2,
                        tp3=t.tp3,
                        filled=False,
                        note=arm.note or arm.status,
                    )
                    retest_rows.append(skip)
                    row["retest"] = asdict(skip)
                    row["delta_vs_baseline"] = round(0.0 - baseline.pnl, 4)
                    details.append(row)
                    continue

                fill = Decimal(str(arm.fill_price))
                # Keep original SL; rebuild TPs as 2/4/6R from new entry
                new_tp1, new_tp2, new_tp3 = _levels_from_entry_sl(fill, stop, is_long)
                # If SL is on wrong side of fill (retest went through), skip
                if is_long and fill <= stop:
                    skip = ReplayResult(
                        arm="retest",
                        pnl=0.0,
                        fees=0.0,
                        exit_reason="skipped_invalid_sl",
                        entry=float(fill),
                        stop_loss=float(stop),
                        tp1=float(new_tp1),
                        tp2=float(new_tp2),
                        tp3=float(new_tp3),
                        filled=False,
                        note="fill_at_or_below_sl",
                    )
                    retest_rows.append(skip)
                    row["retest"] = asdict(skip)
                    row["delta_vs_baseline"] = round(0.0 - baseline.pnl, 4)
                    details.append(row)
                    continue
                if (not is_long) and fill >= stop:
                    skip = ReplayResult(
                        arm="retest",
                        pnl=0.0,
                        fees=0.0,
                        exit_reason="skipped_invalid_sl",
                        entry=float(fill),
                        stop_loss=float(stop),
                        tp1=float(new_tp1),
                        tp2=float(new_tp2),
                        tp3=float(new_tp3),
                        filled=False,
                        note="fill_at_or_above_sl",
                    )
                    retest_rows.append(skip)
                    row["retest"] = asdict(skip)
                    row["delta_vs_baseline"] = round(0.0 - baseline.pnl, 4)
                    details.append(row)
                    continue

                retest_expiry = ensure_utc(arm.fill_time) + PENDING_MULT * _tf_delta(t.timeframe)
                retest = _replay_from_fill(
                    arm="retest",
                    direction=t.direction,
                    entry=fill,
                    stop=stop,
                    tp1=new_tp1,
                    tp2=new_tp2,
                    tp3=new_tp3,
                    fill_time=ensure_utc(arm.fill_time),
                    candles=candles,
                    expiry_at=retest_expiry,
                )
                retest_rows.append(retest)
                row["retest"] = asdict(retest)
                row["delta_vs_baseline"] = round(retest.pnl - baseline.pnl, 4)
                row["entry_improvement"] = round(float(t.entry - float(fill)) if is_long else float(fill) - t.entry, 6)
                details.append(row)
    finally:
        await container.aclose()

    baseline_agg = _agg(baseline_rows)
    # Retest aggregate: skips count as 0 PnL (opportunity cost vs taking the breakout)
    retest_agg = _agg(retest_rows, count_skips_as_zero=True)
    # Also filled-only view
    retest_filled_only = _agg([r for r in retest_rows if r.filled])

    helps = hurts = same = 0
    for d in details:
        if "delta_vs_baseline" not in d:
            continue
        delta = d["delta_vs_baseline"]
        # Compare retest path (0 if skip) vs baseline
        if delta > 0.01:
            helps += 1
        elif delta < -0.01:
            hurts += 1
        else:
            same += 1

    skip_counts: dict[str, int] = {}
    for r in retest_rows:
        if not r.filled:
            skip_counts[r.exit_reason] = skip_counts.get(r.exit_reason, 0) + 1

    # Highlight UNI and top deltas
    uni_rows = [d for d in details if "UNI" in d["symbol"].upper()]
    ranked = sorted(
        [d for d in details if "delta_vs_baseline" in d],
        key=lambda x: abs(x["delta_vs_baseline"]),
        reverse=True,
    )[:15]

    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "mode": "pending_retest_vs_baseline",
            "zone_long": "reference - [1.0, 0.35]·ATR14",
            "zone_short": "reference + [0.35, 1.0]·ATR14",
            "fill": "zone midpoint on first touch after signal bar",
            "invalidate": "original SL before fill → skip",
            "pending_expiry": f"{PENDING_MULT}× primary TF",
            "after_fill": "keep original SL, rebuild TP 2/4/6R, scale 33/33/34, BE after TP1, expiry from fill",
            "sizing": f"${MARGIN} margin × {LEVERAGE}x, fee {float(FEE)*100}%",
            "live_strategy_changed": False,
        },
        "sample": {
            "total_positions": len(trades),
            "simulated": len(baseline_rows),
            "symbols": sorted({t.symbol for t in trades}),
        },
        "baseline": baseline_agg,
        "retest": retest_agg,
        "retest_filled_only": retest_filled_only,
        "delta_total_pnl": round(retest_agg["total_pnl"] - baseline_agg["total_pnl"], 2),
        "help_hurt": {"helps": helps, "hurts": hurts, "same": same},
        "skip_counts": skip_counts,
        "uni": uni_rows,
        "top_abs_deltas": ranked,
        "trades": details,
    }

    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
