"""Counterfactual paper-trade variant sweep vs baseline 4× expiry IST.

Replays all paper positions bar-by-bar (SL → TP scale-out → BE → expiry) and
compares filter / management / regime variants. Prefers market_candles from
Postgres; falls back to exchange candles.

Outputs one JSON object to stdout. Does not change live config.
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
from app.models.market import Asset, IndicatorSnapshot, MarketCandle
from app.models.signal import Signal
from app.repositories.paper_repository import PaperRepository
from app.signals.risk import DEFAULT_TP_MULTIPLIERS, RiskManager

FEE = Decimal("0.001")
SCALE = (Decimal("0.33333333"), Decimal("0.33333333"), Decimal("0.33333334"))
MOVE_STOP_TO_BE = True
MARGIN = Decimal("100")
LEVERAGE = Decimal("10")
TP_MULTS = tuple(Decimal(str(x)) for x in DEFAULT_TP_MULTIPLIERS)
ZONE_NEAR = Decimal("0.35")
ZONE_FAR = Decimal("1.0")
ATR_PERIOD = 14
PENDING_MULT = 4

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
    signal_id: int | None = None
    signal_created_at: datetime | None = None
    score: float | None = None
    market_phase: str | None = None
    primary_timeframe: str | None = None
    confidence: str | None = None
    adx: float | None = None


@dataclass
class ReplayResult:
    pnl: float
    fees: float
    exit_reason: str
    bars: int = 0
    hold_hours: float = 0.0
    tp1: bool = False
    tp2: bool = False
    tp3: bool = False
    closed: bool = False
    skipped: str | None = None
    entry: float | None = None
    opened_at: str | None = None
    closed_at_sim: str | None = None


@dataclass
class VariantDef:
    key: str
    group: str  # filter | management | regime | control
    label: str
    notes: str = ""
    skip_reason: str | None = None  # if set, variant is not run


def _tf_delta(tf: str) -> timedelta:
    return timedelta(seconds=TF_SECONDS.get(tf, 3600))


def _is_long(direction: str) -> bool:
    return SignalDirection(direction).is_long


def _score_passes(trade: TradeInput, long_min: float, short_max: float) -> bool:
    if trade.score is None:
        return False
    if _is_long(trade.direction):
        return trade.score >= long_min
    return trade.score <= short_max


def _scaled_stop_tps(
    entry: float,
    stop: float,
    is_long: bool,
    sl_mult: float,
) -> tuple[float, float, float, float]:
    risk = abs(entry - stop)
    new_risk = risk * sl_mult
    if is_long:
        new_stop = entry - new_risk
    else:
        new_stop = entry + new_risk
    tp1, tp2, tp3 = RiskManager.targets_from_stop(
        entry, new_stop, is_long=is_long, multipliers=DEFAULT_TP_MULTIPLIERS
    )
    return new_stop, tp1, tp2, tp3


def _levels_from_entry_sl(entry: Decimal, stop: Decimal, is_long: bool) -> tuple[Decimal, Decimal, Decimal]:
    risk = abs(entry - stop)
    if risk <= 0:
        risk = entry * Decimal("0.01")
    direction = Decimal("1") if is_long else Decimal("-1")
    tps = tuple(entry + direction * m * risk for m in TP_MULTS)
    return tps[0], tps[1], tps[2]


def _wilder_atr(candles: list[Candle], end_idx: int, period: int = ATR_PERIOD) -> float | None:
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
        return reference - far, reference - near
    return reference + near, reference + far


def _arm_retest(trade: TradeInput, candles: list[Candle]) -> tuple[float | None, datetime | None, str | None]:
    is_long = _is_long(trade.direction)
    arm_time = ensure_utc(trade.signal_created_at or trade.opened_at)
    reference = Decimal(str(trade.entry))
    stop = Decimal(str(trade.stop_loss))
    pending_until = arm_time + PENDING_MULT * _tf_delta(trade.timeframe)

    sig_idx = None
    for i, c in enumerate(candles):
        if ensure_utc(c.open_time) <= arm_time:
            sig_idx = i
        else:
            break
    if sig_idx is None:
        return None, None, "no_bar_at_signal"

    atr_f = _wilder_atr(candles, sig_idx)
    if atr_f is None:
        return None, None, "no_atr"
    atr = Decimal(str(atr_f))
    zone_lo, zone_hi = _retest_zone(reference, atr, is_long)

    for c in candles[sig_idx + 1 :]:
        when = ensure_utc(c.open_time)
        if when > pending_until:
            return None, None, "pending_expired"
        high = Decimal(str(float(c.high)))
        low = Decimal(str(float(c.low)))
        if is_long and low <= stop:
            return None, None, "sl_before_retest"
        if (not is_long) and high >= stop:
            return None, None, "sl_before_retest"
        if low <= zone_hi and high >= zone_lo:
            fill = (zone_lo + zone_hi) / Decimal("2")
            return float(fill), when, None
    return None, None, "data_ended_before_fill"


def _simulate(
    trade: TradeInput,
    candles: list[Candle],
    *,
    expires_at: datetime | None,
    entry: float | None = None,
    stop_loss: float | None = None,
    tp1: float | None = None,
    tp2: float | None = None,
    tp3: float | None = None,
    opened_at: datetime | None = None,
    qty: float | None = None,
    notional: float | None = None,
    tp_mode: str = "scale_246",  # scale_246 | tp1_only
    be_mode: str = "after_tp1",  # after_tp1 | after_1r | never
    no_expiry: bool = False,
) -> ReplayResult:
    is_long = _is_long(trade.direction)
    entry_f = float(entry if entry is not None else trade.entry)
    stop_f = float(stop_loss if stop_loss is not None else trade.stop_loss)
    tp1_f = float(tp1 if tp1 is not None else trade.tp1)
    tp2_f = float(tp2 if tp2 is not None else trade.tp2)
    tp3_f = float(tp3 if tp3 is not None else trade.tp3)
    open_at = ensure_utc(opened_at if opened_at is not None else trade.opened_at)

    entry_d = Decimal(str(entry_f))
    current_stop = Decimal(str(stop_f))
    tp1_d = Decimal(str(tp1_f))
    tp2_d = Decimal(str(tp2_f))
    tp3_d = Decimal(str(tp3_f))
    risk = abs(entry_d - Decimal(str(stop_f)))
    be_trigger = entry_d + (risk if is_long else -risk)  # 1R

    notional_d = Decimal(str(notional if notional is not None else trade.notional))
    if qty is not None:
        qty0 = Decimal(str(qty))
    else:
        qty0 = notional_d / entry_d if entry_d > 0 else Decimal(str(trade.qty))

    rem = qty0
    realized = Decimal("0")
    fees = Decimal("0")
    entry_fee = notional_d * FEE
    fees += entry_fee
    realized -= entry_fee

    tp1_hit = tp2_hit = tp3_hit = False
    exit_reason = "open"
    bars = 0
    closed = False
    closed_at_sim: datetime | None = None

    def reduce(price: Decimal, fraction: Decimal | None, reason: str, when: datetime, *, all_rest: bool = False) -> None:
        nonlocal rem, realized, fees, exit_reason, closed, closed_at_sim
        if rem <= 0:
            return
        q = rem if all_rest or fraction is None else min(qty0 * fraction, rem)
        if q <= 0:
            return
        direction = Decimal("1") if is_long else Decimal("-1")
        gross = (price - entry_d) * q * direction
        fee = price * q * FEE
        rem -= q
        realized += gross - fee
        fees += fee
        exit_reason = reason
        closed_at_sim = when
        if rem <= Decimal("0.00000001"):
            rem = Decimal("0")
            closed = True

    exp = None if no_expiry else (ensure_utc(expires_at) if expires_at else None)

    for c in candles:
        if rem <= 0:
            break
        when = ensure_utc(c.open_time)
        if when < open_at:
            continue
        bars += 1
        high = Decimal(str(float(c.high)))
        low = Decimal(str(float(c.low)))
        close = Decimal(str(float(c.close)))

        stop_hit = low <= current_stop if is_long else high >= current_stop
        if stop_hit:
            reason = "break_even" if current_stop == entry_d else "stop_loss"
            reduce(current_stop, None, reason, when, all_rest=True)
            break

        fav = high if is_long else low

        if be_mode == "after_1r" and current_stop != entry_d:
            hit_1r = fav >= be_trigger if is_long else fav <= be_trigger
            if hit_1r:
                current_stop = entry_d

        if tp_mode == "tp1_only":
            hit = fav >= tp1_d if is_long else fav <= tp1_d
            if hit:
                reduce(tp1_d, None, "take_profit_1", when, all_rest=True)
                tp1_hit = True
                break
        else:
            if not tp1_hit:
                hit = fav >= tp1_d if is_long else fav <= tp1_d
                if hit:
                    reduce(tp1_d, SCALE[0], "take_profit_1", when)
                    tp1_hit = True
                    if MOVE_STOP_TO_BE and be_mode == "after_tp1":
                        current_stop = entry_d
            if tp1_hit and not tp2_hit and rem > 0:
                hit = fav >= tp2_d if is_long else fav <= tp2_d
                if hit:
                    reduce(tp2_d, SCALE[1], "take_profit_2", when)
                    tp2_hit = True
            if tp2_hit and not tp3_hit and rem > 0:
                hit = fav >= tp3_d if is_long else fav <= tp3_d
                if hit:
                    reduce(tp3_d, None, "take_profit_3", when, all_rest=True)
                    tp3_hit = True
                    break

        if rem <= 0:
            break

        if exp is not None and when >= exp and rem > 0:
            reduce(close, None, "expired", when, all_rest=True)
            break

    if rem > 0:
        last = Decimal(str(float(candles[-1].close))) if candles else entry_d
        when = ensure_utc(candles[-1].open_time) if candles else open_at
        reduce(last, None, "data_end_mtm", when, all_rest=True)
        closed = False

    hold_hours = 0.0
    if closed_at_sim is not None:
        hold_hours = max(0.0, (closed_at_sim - open_at).total_seconds() / 3600.0)
    elif bars > 0:
        hold_hours = bars * TF_SECONDS.get(trade.timeframe, 3600) / 3600.0

    return ReplayResult(
        pnl=round(float(realized), 4),
        fees=round(float(fees), 4),
        exit_reason=exit_reason,
        bars=bars,
        hold_hours=round(hold_hours, 3),
        tp1=tp1_hit,
        tp2=tp2_hit,
        tp3=tp3_hit,
        closed=closed or exit_reason not in {"open", "data_end_mtm"},
        entry=entry_f,
        opened_at=open_at.isoformat(),
        closed_at_sim=closed_at_sim.isoformat() if closed_at_sim else None,
    )


def _agg(rows: list[ReplayResult], *, baseline_pnl: float | None = None) -> dict[str, Any]:
    taken = [r for r in rows if r.skipped is None]
    pnls = [r.pnl for r in taken]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = sum(losses)
    holds = [r.hold_hours for r in taken if r.hold_hours > 0]
    out: dict[str, Any] = {
        "n": len(taken),
        "n_skipped": sum(1 for r in rows if r.skipped is not None),
        "total_pnl": round(sum(pnls), 2) if pnls else 0.0,
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "wins": len(wins),
        "losses": len(losses),
        "flats": sum(1 for p in pnls if p == 0),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
        "profit_factor": round(gross_win / gross_loss, 4)
        if gross_loss > 0
        else (99.0 if gross_win > 0 else 0.0),
        "avg_hold_h": round(sum(holds) / len(holds), 2) if holds else 0.0,
        "exit_counts": {},
        "skip_counts": {},
    }
    for r in taken:
        out["exit_counts"][r.exit_reason] = out["exit_counts"].get(r.exit_reason, 0) + 1
    for r in rows:
        if r.skipped:
            out["skip_counts"][r.skipped] = out["skip_counts"].get(r.skipped, 0) + 1
    if baseline_pnl is not None:
        out["delta_vs_baseline"] = round(out["total_pnl"] - baseline_pnl, 2)
    return out


async def _load_candles_db(session, symbol: str, timeframe: str, start: datetime) -> list[Candle]:
    asset = (
        await session.execute(select(Asset).where(Asset.symbol == symbol.upper()))
    ).scalar_one_or_none()
    if asset is None:
        return []
    start_utc = ensure_utc(start) - timedelta(hours=48)
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


async def _load_candles(session, provider, symbol: str, timeframe: str, start: datetime) -> tuple[list[Candle], str]:
    db_candles = await _load_candles_db(session, symbol, timeframe, start)
    # Need history before open for ATR/retest; prefer DB when reasonably dense
    usable_after = [c for c in db_candles if ensure_utc(c.open_time) >= ensure_utc(start)]
    if len(usable_after) >= 3:
        return db_candles, "db"
    try:
        live = await provider.get_candles(
            symbol,
            timeframe,
            limit=100_000,
            start_time=ensure_utc(start) - timedelta(hours=48),
            end_time=utc_now(),
        )
        return list(live.candles), "exchange"
    except Exception as exc:  # noqa: BLE001
        print(f"  candle miss {symbol} {timeframe}: {exc}", file=sys.stderr)
        return db_candles, "db_sparse"


def _btc_allows(trade: TradeInput, btc_closes: list[tuple[datetime, float]]) -> bool:
    """Long only if BTC close > SMA20; short only if BTC close < SMA20."""
    if not btc_closes:
        return True
    t = ensure_utc(trade.opened_at)
    window: list[float] = []
    last_close: float | None = None
    for when, close in btc_closes:
        if when <= t:
            window.append(close)
            last_close = close
        else:
            break
    if last_close is None or len(window) < 20:
        return True  # insufficient → do not filter
    sma = sum(window[-20:]) / 20.0
    if _is_long(trade.direction):
        return last_close >= sma
    return last_close <= sma


def _resolve_expires(trade: TradeInput) -> datetime | None:
    if trade.expires_at is not None:
        return ensure_utc(trade.expires_at)
    base = trade.signal_created_at or trade.opened_at
    return ensure_utc(base) + 4 * _tf_delta(trade.timeframe)


def _variant_catalog(trades: list[TradeInput]) -> list[VariantDef]:
    tfs = {t.timeframe for t in trades}
    phases = {t.market_phase for t in trades if t.market_phase}
    dirs = {t.direction for t in trades}
    has_non_strong = any(d not in {"STRONG_LONG", "STRONG_SHORT"} for d in dirs)
    has_range = "RANGE" in phases
    has_4h = any(tf == "4h" for tf in tfs) or any(t.primary_timeframe == "4h" for t in trades)

    variants: list[VariantDef] = [
        VariantDef("baseline_4x", "control", "Baseline 4× expiry (replay)", "Control: recorded levels, 4× TF expiry, scale 33/33/34, BE after TP1"),
        VariantDef("recorded_actual", "control", "Recorded paper ledger", "Actual closed PnL from DB (no replay)"),
        VariantDef("score_ge_80_le_20", "filter", "Score long≥80 / short≤20", "STRONGER score gate on existing paper set"),
        VariantDef("score_ge_85_le_15", "filter", "Score long≥85 / short≤15", "Stricter score gate"),
        VariantDef("long_only", "filter", "Long-only", "Keep STRONG_LONG / LONG only"),
        VariantDef("short_only", "filter", "Short-only", "Keep STRONG_SHORT / SHORT only"),
        VariantDef("adx_ge_25", "filter", "ADX ≥ 25", "Nearest indicator snapshot ADX"),
        VariantDef("adx_ge_30", "filter", "ADX ≥ 30", "Nearest indicator snapshot ADX"),
        VariantDef(
            "exclude_range",
            "filter",
            "Exclude RANGE phase",
            "Skip market_phase=RANGE",
            skip_reason=None if has_range else "no_RANGE_in_paper_set",
        ),
        VariantDef(
            "strong_only",
            "filter",
            "STRONG direction only",
            "Already enforced in paper",
            skip_reason=None if has_non_strong else "paper_already_strong_only",
        ),
        VariantDef(
            "tf_4h_only",
            "filter",
            "4h signals only",
            "Filter to 4h primary/position TF",
            skip_reason=None if has_4h else "all_positions_are_1h_no_4h_subset",
        ),
        VariantDef("cooldown_1_open_per_symbol", "filter", "Max 1 concurrent open / symbol", "Skip if symbol still open (recorded close)"),
        VariantDef("first_per_symbol", "filter", "First trade per symbol only", "Keep earliest open per symbol"),
        VariantDef("sl_0_75x", "management", "Tighter SL 0.75× R", "Rescale SL & 2/4/6R TPs from entry"),
        VariantDef("sl_1_25x", "management", "Wider SL 1.25× R", "Rescale SL & 2/4/6R TPs from entry"),
        VariantDef("tp1_only", "management", "TP1-only full exit", "Close 100% at TP1; no scale-out"),
        VariantDef("be_after_1r", "management", "BE after 1R", "Move stop to entry at +1R (before TP1)"),
        VariantDef("retest_entry", "management", "Retest / pullback entry", "ATR pullback zone fill; skip if no fill"),
        VariantDef("hold_no_expiry", "management", "No expiry (hold to data end)", "Known weak historically — single cell"),
        VariantDef("delay_entry_30m", "regime", "Delay entry +30m", "Enter 30m later at bar open; skip if SL first"),
        VariantDef("btc_sma20_filter", "regime", "BTC SMA20 filter", "Long only if BTC≥SMA20; short if BTC≤SMA20"),
    ]
    return variants


async def _load_trades(session, container) -> list[TradeInput]:
    account = await container.paper_trading.get_or_create_account(session)
    positions = await PaperRepository(session).list_positions(account.id)

    signal_ids = [p.signal_id for p in positions if p.signal_id]
    signal_map: dict[int, Signal] = {}
    if signal_ids:
        rows = (
            await session.execute(select(Signal).where(Signal.id.in_(signal_ids)))
        ).scalars()
        signal_map = {int(s.id): s for s in rows}

    # Nearest ADX snapshot per position
    adx_map: dict[int, float] = {}
    for p in positions:
        if p.asset_id is None:
            continue
        row = (
            await session.execute(
                select(IndicatorSnapshot.adx_14)
                .where(
                    IndicatorSnapshot.asset_id == p.asset_id,
                    IndicatorSnapshot.timeframe == (p.timeframe or "1h"),
                    IndicatorSnapshot.candle_open_time <= p.opened_at,
                    IndicatorSnapshot.adx_14.is_not(None),
                )
                .order_by(IndicatorSnapshot.candle_open_time.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            adx_map[int(p.id)] = float(row)

    trades: list[TradeInput] = []
    for p in positions:
        sig = signal_map.get(int(p.signal_id)) if p.signal_id else None
        score = float(p.signal_score) if p.signal_score is not None else (float(sig.score) if sig else None)
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
                signal_id=int(p.signal_id) if p.signal_id else None,
                signal_created_at=ensure_utc(sig.created_at) if sig else None,
                score=score,
                market_phase=sig.market_phase if sig else None,
                primary_timeframe=sig.primary_timeframe if sig else None,
                confidence=sig.confidence if sig else None,
                adx=adx_map.get(int(p.id)),
            )
        )
    trades.sort(key=lambda t: t.opened_at)
    return trades


def _filter_mask(trades: list[TradeInput], key: str, btc_closes: list[tuple[datetime, float]]) -> list[bool]:
    """True = keep trade."""
    n = len(trades)
    keep = [True] * n

    if key == "score_ge_80_le_20":
        return [_score_passes(t, 80.0, 20.0) for t in trades]
    if key == "score_ge_85_le_15":
        return [_score_passes(t, 85.0, 15.0) for t in trades]
    if key == "long_only":
        return [_is_long(t.direction) for t in trades]
    if key == "short_only":
        return [not _is_long(t.direction) for t in trades]
    if key == "adx_ge_25":
        return [t.adx is not None and t.adx >= 25 for t in trades]
    if key == "adx_ge_30":
        return [t.adx is not None and t.adx >= 30 for t in trades]
    if key == "exclude_range":
        return [(t.market_phase or "") != "RANGE" for t in trades]
    if key == "strong_only":
        return [t.direction in {"STRONG_LONG", "STRONG_SHORT"} for t in trades]
    if key == "tf_4h_only":
        return [(t.timeframe == "4h" or t.primary_timeframe == "4h") for t in trades]
    if key == "first_per_symbol":
        seen: set[str] = set()
        out: list[bool] = []
        for t in trades:
            sym = t.symbol.upper()
            if sym in seen:
                out.append(False)
            else:
                seen.add(sym)
                out.append(True)
        return out
    if key == "cooldown_1_open_per_symbol":
        # Use recorded open/close intervals
        open_until: dict[str, datetime] = {}
        out = []
        for t in trades:
            sym = t.symbol.upper()
            opened = ensure_utc(t.opened_at)
            busy_until = open_until.get(sym)
            if busy_until is not None and opened < busy_until:
                out.append(False)
                continue
            out.append(True)
            closed = ensure_utc(t.closed_at) if t.closed_at else opened + 4 * _tf_delta(t.timeframe)
            open_until[sym] = closed
        return out
    if key == "btc_sma20_filter":
        return [_btc_allows(t, btc_closes) for t in trades]
    return keep


async def main() -> int:
    settings = get_settings()
    # Keep stdout JSON-clean (logging → stderr).
    configure_logging(settings.log_level, json_output=False)
    import logging

    for name in ("app", "asyncio", "sqlalchemy"):
        logging.getLogger(name).handlers.clear()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    container = build_container(settings)

    async with session_scope() as session:
        trades = await _load_trades(session, container)
        print(f"Loaded {len(trades)} paper positions", file=sys.stderr)

        catalog = _variant_catalog(trades)
        candle_cache: dict[tuple[str, str], tuple[list[Candle], str]] = {}
        earliest = min((t.opened_at for t in trades), default=utc_now()) - timedelta(hours=48)

        # Prefetch candles per symbol/tf
        for t in trades:
            key = (t.symbol.upper(), t.timeframe)
            if key in candle_cache:
                continue
            candles, src = await _load_candles(session, container.provider, t.symbol, t.timeframe, t.opened_at)
            # Also ensure history from earliest for retest ATR
            if not candles or ensure_utc(candles[0].open_time) > ensure_utc(t.signal_created_at or t.opened_at) - timedelta(hours=24):
                candles2, src2 = await _load_candles(session, container.provider, t.symbol, t.timeframe, earliest)
                if len(candles2) > len(candles):
                    candles, src = candles2, src2
            candle_cache[key] = (candles, src)
            print(f"  candles {key[0]} {key[1]}: {len(candles)} ({src})", file=sys.stderr)

        # BTC for regime filter
        btc_candles, btc_src = await _load_candles(session, container.provider, "BTCUSDT", "1h", earliest)
        btc_closes = [(ensure_utc(c.open_time), float(c.close)) for c in btc_candles]
        print(f"  BTCUSDT 1h: {len(btc_closes)} ({btc_src})", file=sys.stderr)

    await container.aclose()

    # Baseline replay for every trade
    baseline_rows: list[ReplayResult] = []
    for t in trades:
        candles, _src = candle_cache.get((t.symbol.upper(), t.timeframe), ([], ""))
        usable = [c for c in candles if ensure_utc(c.open_time) >= t.opened_at]
        if len(usable) < 1:
            baseline_rows.append(ReplayResult(pnl=0.0, fees=0.0, exit_reason="skipped", skipped="no_candles"))
            continue
        baseline_rows.append(
            _simulate(t, candles, expires_at=_resolve_expires(t))
        )

    baseline_agg = _agg(baseline_rows)
    baseline_pnl = baseline_agg["total_pnl"]
    recorded_pnl = round(sum(t.actual_pnl for t in trades if t.status == "closed"), 2)

    results: list[dict[str, Any]] = []
    per_variant_detail: dict[str, list[dict[str, Any]]] = {}

    for v in catalog:
        if v.key == "recorded_actual":
            rows = [
                ReplayResult(
                    pnl=float(t.actual_pnl),
                    fees=float(t.actual_fees),
                    exit_reason=t.actual_exit or "unknown",
                    hold_hours=(
                        (ensure_utc(t.closed_at) - ensure_utc(t.opened_at)).total_seconds() / 3600.0
                        if t.closed_at
                        else 0.0
                    ),
                    closed=t.status == "closed",
                )
                for t in trades
                if t.status == "closed"
            ]
            agg = _agg(rows, baseline_pnl=baseline_pnl)
            results.append({**asdict(v), **agg, "status": "ok"})
            continue

        if v.skip_reason:
            results.append(
                {
                    **asdict(v),
                    "n": 0,
                    "total_pnl": None,
                    "delta_vs_baseline": None,
                    "win_rate": None,
                    "profit_factor": None,
                    "status": "skipped",
                    "skip_reason": v.skip_reason,
                }
            )
            continue

        rows: list[ReplayResult] = []

        if v.group == "filter" or v.key == "btc_sma20_filter":
            mask = _filter_mask(trades, v.key, btc_closes)
            for t, keep, base in zip(trades, mask, baseline_rows, strict=True):
                if not keep:
                    rows.append(ReplayResult(pnl=0.0, fees=0.0, exit_reason="filtered", skipped="filtered"))
                    continue
                if base.skipped:
                    rows.append(ReplayResult(pnl=0.0, fees=0.0, exit_reason="skipped", skipped=base.skipped))
                    continue
                # Reuse baseline path economics for filter cells
                rows.append(base)
        elif v.key == "baseline_4x":
            rows = list(baseline_rows)
        elif v.key == "sl_0_75x":
            for t in trades:
                candles, _ = candle_cache[(t.symbol.upper(), t.timeframe)]
                if not any(ensure_utc(c.open_time) >= t.opened_at for c in candles):
                    rows.append(ReplayResult(pnl=0.0, fees=0.0, exit_reason="skipped", skipped="no_candles"))
                    continue
                stop, tp1, tp2, tp3 = _scaled_stop_tps(t.entry, t.stop_loss, _is_long(t.direction), 0.75)
                rows.append(
                    _simulate(
                        t,
                        candles,
                        expires_at=_resolve_expires(t),
                        stop_loss=stop,
                        tp1=tp1,
                        tp2=tp2,
                        tp3=tp3,
                    )
                )
        elif v.key == "sl_1_25x":
            for t in trades:
                candles, _ = candle_cache[(t.symbol.upper(), t.timeframe)]
                if not any(ensure_utc(c.open_time) >= t.opened_at for c in candles):
                    rows.append(ReplayResult(pnl=0.0, fees=0.0, exit_reason="skipped", skipped="no_candles"))
                    continue
                stop, tp1, tp2, tp3 = _scaled_stop_tps(t.entry, t.stop_loss, _is_long(t.direction), 1.25)
                rows.append(
                    _simulate(
                        t,
                        candles,
                        expires_at=_resolve_expires(t),
                        stop_loss=stop,
                        tp1=tp1,
                        tp2=tp2,
                        tp3=tp3,
                    )
                )
        elif v.key == "tp1_only":
            for t in trades:
                candles, _ = candle_cache[(t.symbol.upper(), t.timeframe)]
                if not any(ensure_utc(c.open_time) >= t.opened_at for c in candles):
                    rows.append(ReplayResult(pnl=0.0, fees=0.0, exit_reason="skipped", skipped="no_candles"))
                    continue
                rows.append(
                    _simulate(
                        t,
                        candles,
                        expires_at=_resolve_expires(t),
                        tp_mode="tp1_only",
                    )
                )
        elif v.key == "be_after_1r":
            for t in trades:
                candles, _ = candle_cache[(t.symbol.upper(), t.timeframe)]
                if not any(ensure_utc(c.open_time) >= t.opened_at for c in candles):
                    rows.append(ReplayResult(pnl=0.0, fees=0.0, exit_reason="skipped", skipped="no_candles"))
                    continue
                rows.append(
                    _simulate(
                        t,
                        candles,
                        expires_at=_resolve_expires(t),
                        be_mode="after_1r",
                    )
                )
        elif v.key == "hold_no_expiry":
            for t in trades:
                candles, _ = candle_cache[(t.symbol.upper(), t.timeframe)]
                if not any(ensure_utc(c.open_time) >= t.opened_at for c in candles):
                    rows.append(ReplayResult(pnl=0.0, fees=0.0, exit_reason="skipped", skipped="no_candles"))
                    continue
                rows.append(_simulate(t, candles, expires_at=None, no_expiry=True))
        elif v.key == "retest_entry":
            for t in trades:
                candles, _ = candle_cache[(t.symbol.upper(), t.timeframe)]
                fill, fill_time, reason = _arm_retest(t, candles)
                if fill is None or fill_time is None:
                    rows.append(
                        ReplayResult(pnl=0.0, fees=0.0, exit_reason="skipped", skipped=reason or "no_fill")
                    )
                    continue
                is_long = _is_long(t.direction)
                entry_d = Decimal(str(fill))
                # Keep original R distance from paper entry/stop, anchored at new fill
                old_risk = abs(Decimal(str(t.entry)) - Decimal(str(t.stop_loss)))
                stop_d = entry_d - old_risk if is_long else entry_d + old_risk
                tp1_d, tp2_d, tp3_d = _levels_from_entry_sl(entry_d, stop_d, is_long)
                notional = float(MARGIN * LEVERAGE)
                qty = notional / fill if fill > 0 else t.qty
                # Expiry from signal time still 4×
                exp = _resolve_expires(t)
                rows.append(
                    _simulate(
                        t,
                        candles,
                        expires_at=exp,
                        entry=fill,
                        stop_loss=float(stop_d),
                        tp1=float(tp1_d),
                        tp2=float(tp2_d),
                        tp3=float(tp3_d),
                        opened_at=fill_time,
                        qty=qty,
                        notional=notional,
                    )
                )
        elif v.key == "delay_entry_30m":
            for t in trades:
                candles, _ = candle_cache[(t.symbol.upper(), t.timeframe)]
                delay_at = ensure_utc(t.opened_at) + timedelta(minutes=30)
                # Skip if SL touched before delayed entry
                is_long = _is_long(t.direction)
                stop = Decimal(str(t.stop_loss))
                invalidated = False
                entry_px: float | None = None
                for c in candles:
                    when = ensure_utc(c.open_time)
                    if when < ensure_utc(t.opened_at):
                        continue
                    if when >= delay_at:
                        entry_px = float(c.open)
                        break
                    high = Decimal(str(float(c.high)))
                    low = Decimal(str(float(c.low)))
                    if is_long and low <= stop:
                        invalidated = True
                        break
                    if (not is_long) and high >= stop:
                        invalidated = True
                        break
                if invalidated or entry_px is None:
                    rows.append(
                        ReplayResult(
                            pnl=0.0,
                            fees=0.0,
                            exit_reason="skipped",
                            skipped="sl_before_delay" if invalidated else "no_bar_after_delay",
                        )
                    )
                    continue
                # Keep original stop/TPs (path from delayed fill); risk not re-anchored
                notional = float(MARGIN * LEVERAGE)
                qty = notional / entry_px if entry_px > 0 else t.qty
                rows.append(
                    _simulate(
                        t,
                        candles,
                        expires_at=_resolve_expires(t),
                        entry=entry_px,
                        opened_at=delay_at,
                        qty=qty,
                        notional=notional,
                    )
                )
        else:
            results.append({**asdict(v), "status": "unhandled"})
            continue

        agg = _agg(rows, baseline_pnl=baseline_pnl)
        results.append({**asdict(v), **agg, "status": "ok"})
        per_variant_detail[v.key] = [
            {
                "id": t.id,
                "symbol": t.symbol,
                "direction": t.direction,
                "score": t.score,
                "adx": t.adx,
                "pnl": r.pnl,
                "exit": r.exit_reason,
                "skipped": r.skipped,
                "hold_h": r.hold_hours,
            }
            for t, r in zip(trades, rows, strict=False)
        ]

    # Rank runnable variants by total_pnl (filters+management+regime), exclude recorded for ranking delta
    runnable = [r for r in results if r.get("status") == "ok" and r.get("key") != "recorded_actual"]
    ranked = sorted(runnable, key=lambda r: (r.get("total_pnl") is not None, r.get("total_pnl") or -1e18), reverse=True)

    # Fidelity baseline vs recorded
    fidelity = []
    for t, r in zip(trades, baseline_rows, strict=True):
        if t.status != "closed" or r.skipped:
            continue
        fidelity.append(
            {
                "id": t.id,
                "symbol": t.symbol,
                "actual": round(t.actual_pnl, 4),
                "sim": r.pnl,
                "diff": round(r.pnl - t.actual_pnl, 4),
                "actual_exit": t.actual_exit,
                "sim_exit": r.exit_reason,
            }
        )
    fidelity_mae = (
        round(sum(abs(f["diff"]) for f in fidelity) / len(fidelity), 4) if fidelity else None
    )

    # Recommendations
    winners = [r for r in ranked if r["key"] != "baseline_4x" and (r.get("delta_vs_baseline") or 0) > 0]
    losers = [r for r in ranked if r["key"] != "baseline_4x" and (r.get("delta_vs_baseline") or 0) <= 0]

    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "economics": "$100 margin × 10x, fee 0.1%, scale 33/33/34, BE after TP1 (unless variant)",
            "baseline": "4× TF expiry replay with recorded entry/SL/TP",
            "candles": "market_candles DB preferred, exchange fallback",
            "filter_accounting": "Skipped trades contribute $0 (not taken)",
            "notes": [
                "Small-n paper set; high overfitting risk",
                "Short calendar window — do not promote without larger backtest",
                "Replay may differ slightly from recorded ledger (timing/fees)",
            ],
        },
        "sample": {
            "n_positions": len(trades),
            "closed": sum(1 for t in trades if t.status == "closed"),
            "open": sum(1 for t in trades if t.status == "open"),
            "longs": sum(1 for t in trades if _is_long(t.direction)),
            "shorts": sum(1 for t in trades if not _is_long(t.direction)),
            "with_score": sum(1 for t in trades if t.score is not None),
            "with_adx": sum(1 for t in trades if t.adx is not None),
            "phases": sorted({t.market_phase for t in trades if t.market_phase}),
            "timeframes": sorted({t.timeframe for t in trades}),
            "first_open": min((t.opened_at for t in trades), default=None),
            "last_open": max((t.opened_at for t in trades), default=None),
            "recorded_pnl": recorded_pnl,
            "avg_score_long": round(
                sum(t.score for t in trades if t.score is not None and _is_long(t.direction))
                / max(1, sum(1 for t in trades if t.score is not None and _is_long(t.direction))),
                2,
            ),
            "avg_score_short": round(
                sum(t.score for t in trades if t.score is not None and not _is_long(t.direction))
                / max(1, sum(1 for t in trades if t.score is not None and not _is_long(t.direction))),
                2,
            ),
            "avg_adx": round(
                sum(t.adx for t in trades if t.adx is not None)
                / max(1, sum(1 for t in trades if t.adx is not None)),
                2,
            ),
        },
        "baseline": {
            "key": "baseline_4x",
            "recorded_pnl": recorded_pnl,
            "replay_pnl": baseline_pnl,
            "replay_mae_vs_recorded": fidelity_mae,
            **{k: baseline_agg[k] for k in ("n", "win_rate", "profit_factor", "avg_hold_h", "exit_counts")},
        },
        "variants": results,
        "ranked": [
            {
                "rank": i + 1,
                "key": r["key"],
                "group": r["group"],
                "label": r["label"],
                "n": r.get("n"),
                "total_pnl": r.get("total_pnl"),
                "delta_vs_baseline": r.get("delta_vs_baseline"),
                "win_rate": r.get("win_rate"),
                "profit_factor": r.get("profit_factor"),
                "avg_hold_h": r.get("avg_hold_h"),
                "notes": r.get("notes"),
                "n_skipped": r.get("n_skipped"),
            }
            for i, r in enumerate(ranked)
        ],
        "candidates": winners[:5],
        "killed": [
            {
                "key": r["key"],
                "label": r["label"],
                "delta_vs_baseline": r.get("delta_vs_baseline"),
                "total_pnl": r.get("total_pnl"),
                "reason": "worse_or_equal_vs_baseline",
            }
            for r in losers[:12]
        ],
        "skipped_variants": [r for r in results if r.get("status") == "skipped"],
        "fidelity_worst": sorted(fidelity, key=lambda x: abs(x["diff"]), reverse=True)[:8],
    }

    for k in ("first_open", "last_open"):
        v = payload["sample"][k]
        if isinstance(v, datetime):
            payload["sample"][k] = v.isoformat()

    print(json.dumps(payload, indent=2, default=str))
    print(
        f"BASELINE replay={baseline_pnl:+.2f} recorded={recorded_pnl:+.2f} | "
        + " | ".join(
            f"{r['key']}={r.get('total_pnl'):+.2f} (Δ{r.get('delta_vs_baseline'):+.2f})"
            for r in ranked[:6]
            if r.get("total_pnl") is not None and r.get("delta_vs_baseline") is not None
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
