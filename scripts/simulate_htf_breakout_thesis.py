"""Counterfactual HTF breakout thesis vs current paper entries.

Thesis (user): Long only after a confirmed 4h candle *close* above resistance;
Short only after 4h close below support. SL near structure (swing low/high),
not a chase fill inside the level.

Resistance/Support: max high / min low of prior LOOKBACK 4h bars (excluding
the signal bar). Pending wait up to PENDING_DAYS for confirmation.
Baseline: fill at historical paper entry (current strategy).

Uses shared app.signals.htf_breakout. JSON to stdout.
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
from app.repositories.paper_repository import PaperRepository

FEE = Decimal("0.001")
SCALE = (Decimal("0.33333333"), Decimal("0.33333333"), Decimal("0.33333334"))
MOVE_STOP_TO_BE = True
MARGIN = Decimal("100")
LEVERAGE = Decimal("10")
TP_MULTIPLIERS = (Decimal("2"), Decimal("4"), Decimal("6"))

from app.signals.htf_breakout import (  # noqa: E402
    DEFAULT_CONFIRM_TF as CONFIRM_TF,
    DEFAULT_LOOKBACK_BARS as LOOKBACK_4H,
    DEFAULT_PENDING_DAYS as PENDING_DAYS,
    HtfArmResult as ArmResult,
    arm_htf_breakout as _arm_htf_breakout_core,
    idx_at_or_before as _idx_at_or_before,
    levels_from_entry_sl as _levels_from_entry_sl,
    wilder_atr as _wilder_atr,
)

EXIT_TF = "1h"
ATR_PERIOD = 14

TF_SECONDS = {
    "1h": 3600,
    "4h": 14400,
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


def _arm_htf_breakout(trade: "TradeInput", candles_4h: list[Candle]) -> ArmResult:
    """Adapter: TradeInput -> shared canonical arm_htf_breakout."""
    from app.signals.htf_breakout import HtfBreakoutConfig

    return _arm_htf_breakout_core(
        direction=trade.direction,
        arm_time=ensure_utc(trade.signal_created_at or trade.opened_at),
        original_stop=float(trade.stop_loss),
        candles_4h=candles_4h,
        config=HtfBreakoutConfig(
            confirm_timeframe=CONFIRM_TF,
            lookback_bars=LOOKBACK_4H,
            pending_days=PENDING_DAYS,
        ),
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
    fees = notional * FEE
    realized -= fees

    current_stop = stop
    tp1_hit = tp2_hit = tp3_hit = False
    exit_reason = "open"
    bars = 0
    note = ""

    def reduce(price: Decimal, fraction: Decimal | None, reason: str, *, all_rest: bool = False) -> None:
        nonlocal rem, realized, fees, exit_reason
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


def _agg(rows: list[ReplayResult]) -> dict[str, Any]:
    pnls = [r.pnl for r in rows]
    filled = [r for r in rows if r.filled]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    gw, gl = sum(wins), sum(losses)
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
        "win_rate_filled": round(len([r for r in filled if r.pnl > 0]) / len(filled), 4)
        if filled
        else 0.0,
        "profit_factor": round(gw / gl, 4) if gl > 0 else (99.0 if gw > 0 else 0.0),
        "exit_counts": {},
        "tp1_hits": sum(1 for r in rows if r.tp1_hit),
        "tp2_hits": sum(1 for r in rows if r.tp2_hit),
        "tp3_hits": sum(1 for r in rows if r.tp3_hit),
    }


async def _load_candles_db(session, symbol: str, timeframe: str, start: datetime) -> list[Candle]:
    asset = (
        await session.execute(select(Asset).where(Asset.symbol == symbol.upper()))
    ).scalar_one_or_none()
    if asset is None:
        return []
    result = await session.execute(
        select(MarketCandle)
        .where(
            MarketCandle.asset_id == asset.id,
            MarketCandle.timeframe == timeframe,
            MarketCandle.is_closed.is_(True),
            MarketCandle.open_time >= ensure_utc(start),
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


async def _load_candles(session, provider, symbol: str, timeframe: str, start: datetime) -> tuple[list[Candle], str]:
    db = await _load_candles_db(session, symbol, timeframe, start)
    need = 40 if timeframe == CONFIRM_TF else 5
    if len(db) >= need:
        return db, "db"
    try:
        live = await provider.get_candles(
            symbol,
            timeframe,
            limit=100_000,
            start_time=start,
            end_time=utc_now(),
        )
        return list(live.candles), "exchange"
    except Exception as exc:  # noqa: BLE001
        print(f"  candle miss {symbol} {timeframe}: {exc}", file=sys.stderr)
        return db, "db_sparse"


async def main() -> int:
    logging.disable(logging.INFO)
    settings = get_settings()
    configure_logging("ERROR", json_output=False)
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

    cache_1h: dict[str, tuple[list[Candle], str]] = {}
    cache_4h: dict[str, tuple[list[Candle], str]] = {}
    baseline_rows: list[ReplayResult] = []
    thesis_rows: list[ReplayResult] = []
    details: list[dict[str, Any]] = []

    try:
        async with session_scope() as session:
            for t in trades:
                sym = t.symbol.upper()
                hist_start = ensure_utc(t.opened_at) - timedelta(days=45)

                if sym not in cache_1h:
                    c1, s1 = await _load_candles(session, container.provider, t.symbol, EXIT_TF, hist_start)
                    cache_1h[sym] = (c1, s1)
                    print(f"  1h {sym}: {len(c1)} ({s1})", file=sys.stderr)
                if sym not in cache_4h:
                    c4, s4 = await _load_candles(session, container.provider, t.symbol, CONFIRM_TF, hist_start)
                    cache_4h[sym] = (c4, s4)
                    print(f"  4h {sym}: {len(c4)} ({s4})", file=sys.stderr)

                candles_1h, src1 = cache_1h[sym]
                candles_4h, src4 = cache_4h[sym]
                is_long = SignalDirection(t.direction).is_long
                entry = Decimal(str(t.entry))
                stop = Decimal(str(t.stop_loss))
                tp1, tp2, tp3 = Decimal(str(t.tp1)), Decimal(str(t.tp2)), Decimal(str(t.tp3))
                fill_time = ensure_utc(t.opened_at)
                baseline_expiry = (
                    ensure_utc(t.expires_at)
                    if t.expires_at
                    else fill_time + 4 * _tf_delta(t.timeframe)
                )

                row: dict[str, Any] = {
                    "id": t.id,
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "status": t.status,
                    "paper_entry": t.entry,
                    "actual_pnl": round(t.actual_pnl, 4),
                    "actual_exit": t.actual_exit,
                    "src_1h": src1,
                    "src_4h": src4,
                }

                usable = [c for c in candles_1h if ensure_utc(c.open_time) >= fill_time]
                if len(usable) < 1 or len(candles_4h) < 15:
                    row["skipped"] = "no_candles"
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
                    candles=candles_1h,
                    expiry_at=baseline_expiry,
                )
                baseline_rows.append(baseline)
                row["baseline"] = asdict(baseline)

                arm = _arm_htf_breakout(t, candles_4h)
                row["thesis_arm"] = asdict(arm)

                if arm.status != "filled" or arm.fill_price is None or arm.fill_time is None or arm.stop is None:
                    skip = ReplayResult(
                        arm="thesis",
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
                    thesis_rows.append(skip)
                    row["thesis"] = asdict(skip)
                    row["delta_vs_baseline"] = round(0.0 - baseline.pnl, 4)
                    details.append(row)
                    continue

                fill = Decimal(str(arm.fill_price))
                new_stop = Decimal(str(arm.stop))
                if is_long and new_stop >= fill:
                    skip = ReplayResult(
                        arm="thesis",
                        pnl=0.0,
                        fees=0.0,
                        exit_reason="skipped_invalid_sl",
                        entry=float(fill),
                        stop_loss=float(new_stop),
                        tp1=t.tp1,
                        tp2=t.tp2,
                        tp3=t.tp3,
                        filled=False,
                        note="stop_not_below_entry",
                    )
                    thesis_rows.append(skip)
                    row["thesis"] = asdict(skip)
                    row["delta_vs_baseline"] = round(0.0 - baseline.pnl, 4)
                    details.append(row)
                    continue
                if (not is_long) and new_stop <= fill:
                    skip = ReplayResult(
                        arm="thesis",
                        pnl=0.0,
                        fees=0.0,
                        exit_reason="skipped_invalid_sl",
                        entry=float(fill),
                        stop_loss=float(new_stop),
                        tp1=t.tp1,
                        tp2=t.tp2,
                        tp3=t.tp3,
                        filled=False,
                        note="stop_not_above_entry",
                    )
                    thesis_rows.append(skip)
                    row["thesis"] = asdict(skip)
                    row["delta_vs_baseline"] = round(0.0 - baseline.pnl, 4)
                    details.append(row)
                    continue

                ntp1, ntp2, ntp3 = _levels_from_entry_sl(fill, new_stop, is_long=is_long)
                thesis_expiry = ensure_utc(arm.fill_time) + 4 * _tf_delta(EXIT_TF)
                # Longer hold after confirmed HTF break — 12× 1h ≈ half day is tight;
                # use 24× 1h (1 day) * 4 from user expiry spirit → 4×4h = 16h from fill on 1h clock:
                thesis_expiry = ensure_utc(arm.fill_time) + 4 * _tf_delta(CONFIRM_TF)

                thesis = _replay_from_fill(
                    arm="thesis",
                    direction=t.direction,
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
                row["thesis"] = asdict(thesis)
                row["delta_vs_baseline"] = round(thesis.pnl - baseline.pnl, 4)
                details.append(row)
    finally:
        await container.aclose()

    base_agg = _agg(baseline_rows)
    # recount exits
    for label, rows in (("baseline", baseline_rows), ("thesis", thesis_rows)):
        ec: dict[str, int] = {}
        for r in rows:
            ec[r.exit_reason] = ec.get(r.exit_reason, 0) + 1
        if label == "baseline":
            base_agg["exit_counts"] = ec
        else:
            pass
    thesis_agg = _agg(thesis_rows)
    ec2: dict[str, int] = {}
    for r in thesis_rows:
        ec2[r.exit_reason] = ec2.get(r.exit_reason, 0) + 1
    thesis_agg["exit_counts"] = ec2
    thesis_filled = _agg([r for r in thesis_rows if r.filled])

    skip_counts: dict[str, int] = {}
    for r in thesis_rows:
        if not r.filled:
            skip_counts[r.exit_reason] = skip_counts.get(r.exit_reason, 0) + 1

    helps = hurts = same = 0
    for d in details:
        if "delta_vs_baseline" not in d:
            continue
        delta = d["delta_vs_baseline"]
        if delta > 0.01:
            helps += 1
        elif delta < -0.01:
            hurts += 1
        else:
            same += 1

    uni = [d for d in details if "UNI" in d["symbol"].upper()]
    ranked = sorted(
        [d for d in details if "delta_vs_baseline" in d],
        key=lambda x: abs(x["delta_vs_baseline"]),
        reverse=True,
    )[:20]

    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "thesis": "4h close beyond lookback resistance/support before fill",
            "lookback_4h_bars": LOOKBACK_4H,
            "pending_days": PENDING_DAYS,
            "sl": "structure swing + failed-break near level",
            "tp": "2/4/6R from new entry/sl",
            "expiry_after_fill": "4× 4h",
            "sizing": "$100×10x fee 0.1%",
            "live_changed": False,
        },
        "sample": {
            "total_positions": len(trades),
            "simulated": len(baseline_rows),
            "symbols": sorted({t.symbol for t in trades}),
        },
        "baseline": base_agg,
        "thesis": thesis_agg,
        "thesis_filled_only": thesis_filled,
        "delta_total_pnl": round(thesis_agg["total_pnl"] - base_agg["total_pnl"], 2),
        "help_hurt": {"helps": helps, "hurts": hurts, "same": same},
        "skip_counts": skip_counts,
        "uni": uni,
        "top_abs_deltas": ranked,
        "trades": details,
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
