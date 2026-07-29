"""Counterfactual SIGNAL_EXPIRY simulation against all paper positions.

Replays each paper trade bar-by-bar with the same SL / TP scale-out / BE rules,
varying only the expiry policy. Prefers market_candles from Postgres; falls
back to exchange candles when DB coverage is insufficient.

Outputs a single JSON object to stdout (summary + per-trade detail).
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
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
from app.repositories.paper_repository import PaperRepository

FEE = Decimal("0.001")  # 0.1% paper_fee_percent
SCALE = (Decimal("0.33333333"), Decimal("0.33333333"), Decimal("0.33333334"))
MOVE_STOP_TO_BE = True

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
class ScenarioResult:
    scenario: str
    pnl: float
    fees: float
    exit_reason: str
    bars: int = 0
    tp1: bool = False
    tp2: bool = False
    tp3: bool = False
    closed: bool = False
    candle_source: str = ""
    delta_vs_actual: float = 0.0
    note: str = ""


@dataclass
class TradeSim:
    trade: dict[str, Any]
    scenarios: dict[str, dict[str, Any]] = field(default_factory=dict)
    candle_count: int = 0
    candle_source: str = ""
    skipped: str | None = None


def _tf_delta(tf: str) -> timedelta:
    secs = TF_SECONDS.get(tf, 3600)
    return timedelta(seconds=secs)


def _resolve_expires(
    trade: TradeInput,
    *,
    multiplier: int | None,
    fixed_hours: float | None,
    no_expiry: bool,
) -> datetime | None:
    if no_expiry:
        return None
    base = trade.signal_created_at or trade.opened_at
    if fixed_hours is not None:
        return ensure_utc(base) + timedelta(hours=fixed_hours)
    if multiplier is not None:
        return ensure_utc(base) + multiplier * _tf_delta(trade.timeframe)
    return ensure_utc(trade.expires_at) if trade.expires_at else None


def _simulate(
    trade: TradeInput,
    candles: list,
    *,
    scenario: str,
    expires_at: datetime | None,
    candle_source: str,
) -> ScenarioResult:
    is_long = SignalDirection(trade.direction).is_long
    entry = Decimal(str(trade.entry))
    current_stop = Decimal(str(trade.stop_loss))
    tp1 = Decimal(str(trade.tp1))
    tp2 = Decimal(str(trade.tp2))
    tp3 = Decimal(str(trade.tp3))
    qty0 = Decimal(str(trade.qty))
    rem = qty0
    realized = Decimal("0")
    fees = Decimal("0")
    # Entry fee (matches paper open)
    entry_fee = Decimal(str(trade.notional)) * FEE
    fees += entry_fee
    realized -= entry_fee

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
        direction = Decimal("1") if is_long else Decimal("-1")
        gross = (price - entry) * qty * direction
        fee = price * qty * FEE
        net = gross - fee
        rem -= qty
        realized += net
        fees += fee
        exit_reason = reason
        if rem <= Decimal("0.00000001"):
            rem = Decimal("0")
            closed = True

    opened = ensure_utc(trade.opened_at)
    for c in candles:
        if rem <= 0:
            break
        when = ensure_utc(c.open_time)
        if when < opened:
            continue
        bars += 1
        high = Decimal(str(float(c.high)))
        low = Decimal(str(float(c.low)))
        close = Decimal(str(float(c.close)))

        # SL first
        stop_hit = low <= current_stop if is_long else high >= current_stop
        if stop_hit:
            reduce(current_stop, None, "stop_loss", all_rest=True)
            break

        # TPs in order (favorable extreme)
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

        # Expiry at bar open_time (matches paper _replay_bars)
        if expires_at is not None and when >= ensure_utc(expires_at) and rem > 0:
            reduce(close, None, "expired", all_rest=True)
            break

    if rem > 0:
        last = Decimal(str(float(candles[-1].close))) if candles else entry
        reduce(last, None, "data_end_mtm", all_rest=True)
        note = "marked_to_market_at_last_candle"
        closed = False  # not a true exit

    pnl = float(realized)
    return ScenarioResult(
        scenario=scenario,
        pnl=round(pnl, 4),
        fees=round(float(fees), 4),
        exit_reason=exit_reason,
        bars=bars,
        tp1=tp1_hit,
        tp2=tp2_hit,
        tp3=tp3_hit,
        closed=closed or exit_reason not in {"open", "data_end_mtm"},
        candle_source=candle_source,
        delta_vs_actual=round(pnl - float(trade.actual_pnl), 4),
        note=note,
    )


SCENARIOS: list[dict[str, Any]] = [
    {"key": "actual_expiry_4x", "label": "Ist (4× TF)", "multiplier": 4, "fixed_hours": None, "no_expiry": False},
    {"key": "multiplier_8x", "label": "8× TF", "multiplier": 8, "fixed_hours": None, "no_expiry": False},
    {"key": "multiplier_12x", "label": "12× TF", "multiplier": 12, "fixed_hours": None, "no_expiry": False},
    {"key": "multiplier_24x", "label": "24× TF", "multiplier": 24, "fixed_hours": None, "no_expiry": False},
    {"key": "fixed_12h", "label": "Fix 12h", "multiplier": None, "fixed_hours": 12, "no_expiry": False},
    {"key": "fixed_24h", "label": "Fix 24h", "multiplier": None, "fixed_hours": 24, "no_expiry": False},
    {"key": "fixed_48h", "label": "Fix 48h", "multiplier": None, "fixed_hours": 48, "no_expiry": False},
    {"key": "no_expiry", "label": "Kein Expiry", "multiplier": None, "fixed_hours": None, "no_expiry": True},
]


def _agg(rows: list[ScenarioResult]) -> dict[str, Any]:
    pnls = [r.pnl for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = sum(losses)
    return {
        "n": len(rows),
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "wins": len(wins),
        "losses": len(losses),
        "flats": sum(1 for p in pnls if p == 0),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
        "profit_factor": round(gross_win / gross_loss, 4)
        if gross_loss > 0
        else (99.0 if gross_win > 0 else 0.0),
        "exit_counts": _count_exits(rows),
        "tp1_hits": sum(1 for r in rows if r.tp1),
        "tp2_hits": sum(1 for r in rows if r.tp2),
        "tp3_hits": sum(1 for r in rows if r.tp3),
        "mtm_open": sum(1 for r in rows if r.exit_reason == "data_end_mtm"),
    }


def _count_exits(rows: list[ScenarioResult]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r.exit_reason] = out.get(r.exit_reason, 0) + 1
    return out


async def _load_candles_db(
    session,
    symbol: str,
    timeframe: str,
    start: datetime,
) -> list:
    asset = (
        await session.execute(select(Asset).where(Asset.symbol == symbol.upper()))
    ).scalar_one_or_none()
    if asset is None:
        return []
    start_utc = ensure_utc(start) - timedelta(hours=1)
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
) -> tuple[list, str]:
    db_candles = await _load_candles_db(session, symbol, timeframe, start)
    if len(db_candles) >= 5:
        return db_candles, "db"

    try:
        live = await provider.get_candles(
            symbol,
            timeframe,
            limit=100_000,
            start_time=start - timedelta(hours=1),
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

        # Join signal created_at when available
        from sqlalchemy import select
        from app.models.signal import Signal

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

    candle_cache: dict[tuple[str, str], tuple[list, str]] = {}
    trade_sims: list[TradeSim] = []
    scenario_rows: dict[str, list[ScenarioResult]] = {s["key"]: [] for s in SCENARIOS}

    try:
        async with session_scope() as session:
            for t in trades:
                key = (t.symbol.upper(), t.timeframe)
                if key not in candle_cache:
                    candles, src = await _load_candles(
                        session, container.provider, t.symbol, t.timeframe, t.opened_at
                    )
                    candle_cache[key] = (candles, src)
                    print(f"  candles {key[0]} {key[1]}: {len(candles)} ({src})", file=sys.stderr)

                candles, src = candle_cache[key]
                ts = TradeSim(
                    trade={
                        "id": t.id,
                        "symbol": t.symbol,
                        "direction": t.direction,
                        "status": t.status,
                        "timeframe": t.timeframe,
                        "opened_at": t.opened_at.isoformat(),
                        "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                        "actual_pnl": round(t.actual_pnl, 4),
                        "actual_exit": t.actual_exit,
                        "entry": t.entry,
                        "stop_loss": t.stop_loss,
                    },
                    candle_count=len(candles),
                    candle_source=src,
                )

                usable = [c for c in candles if ensure_utc(c.open_time) >= t.opened_at]
                if len(usable) < 1:
                    ts.skipped = "no_candles_after_entry"
                    trade_sims.append(ts)
                    continue

                for sc in SCENARIOS:
                    expires = _resolve_expires(
                        t,
                        multiplier=sc["multiplier"],
                        fixed_hours=sc["fixed_hours"],
                        no_expiry=sc["no_expiry"],
                    )
                    # For actual_expiry_4x prefer recorded expires_at when present
                    if sc["key"] == "actual_expiry_4x" and t.expires_at is not None:
                        expires = ensure_utc(t.expires_at)

                    result = _simulate(
                        t,
                        candles,
                        scenario=sc["key"],
                        expires_at=expires,
                        candle_source=src,
                    )
                    ts.scenarios[sc["key"]] = asdict(result)
                    scenario_rows[sc["key"]].append(result)

                trade_sims.append(ts)
    finally:
        await container.aclose()

    # Baseline = recorded actual PnL (not replay) for honesty check
    closed = [t for t in trades if t.status == "closed"]
    open_trades = [t for t in trades if t.status == "open"]
    expired = [t for t in trades if (t.actual_exit or "").lower() in {"expired", "expiry"}]

    # Per-expired trade table comparing actual vs counterfactuals
    expired_detail = []
    for ts in trade_sims:
        if (ts.trade.get("actual_exit") or "").lower() not in {"expired", "expiry"}:
            continue
        row = {
            "id": ts.trade["id"],
            "symbol": ts.trade["symbol"],
            "direction": ts.trade["direction"],
            "tf": ts.trade["timeframe"],
            "actual_pnl": ts.trade["actual_pnl"],
            "skipped": ts.skipped,
        }
        for sc in SCENARIOS:
            s = ts.scenarios.get(sc["key"])
            if s:
                row[sc["key"] + "_pnl"] = s["pnl"]
                row[sc["key"] + "_exit"] = s["exit_reason"]
                row[sc["key"] + "_delta"] = s["delta_vs_actual"]
        expired_detail.append(row)

    # Help vs hurt for expired trades under no_expiry
    help_hurt = {"helps": 0, "hurts": 0, "same": 0, "deltas": []}
    for row in expired_detail:
        d = row.get("no_expiry_delta")
        if d is None:
            continue
        help_hurt["deltas"].append(d)
        if d > 0.01:
            help_hurt["helps"] += 1
        elif d < -0.01:
            help_hurt["hurts"] += 1
        else:
            help_hurt["same"] += 1

    # Scenario aggregates — also include "recorded_actual" as baseline KPI
    recorded_actual_pnl = round(sum(t.actual_pnl for t in closed), 2)
    scenario_summary = []
    for sc in SCENARIOS:
        rows = scenario_rows[sc["key"]]
        agg = _agg(rows)
        agg["key"] = sc["key"]
        agg["label"] = sc["label"]
        agg["delta_vs_recorded"] = round(agg["total_pnl"] - recorded_actual_pnl, 2)
        # vs replayed 4x
        if scenario_rows["actual_expiry_4x"]:
            base = sum(r.pnl for r in scenario_rows["actual_expiry_4x"])
            agg["delta_vs_replay_4x"] = round(agg["total_pnl"] - base, 2)
        scenario_summary.append(agg)

    # Distribution: longer hold help/hurt across ALL simulated closed-path trades
    dist = {}
    base_key = "actual_expiry_4x"
    for sc in SCENARIOS:
        if sc["key"] == base_key:
            continue
        helps = hurts = same = 0
        for ts in trade_sims:
            a = ts.scenarios.get(base_key)
            b = ts.scenarios.get(sc["key"])
            if not a or not b:
                continue
            d = b["pnl"] - a["pnl"]
            if d > 0.01:
                helps += 1
            elif d < -0.01:
                hurts += 1
            else:
                same += 1
        dist[sc["key"]] = {"helps": helps, "hurts": hurts, "same": same}

    # Replay fidelity: actual_expiry_4x vs recorded
    fidelity = []
    for ts in trade_sims:
        if ts.trade["status"] != "closed":
            continue
        sim = ts.scenarios.get("actual_expiry_4x")
        if not sim:
            continue
        fidelity.append(
            {
                "id": ts.trade["id"],
                "symbol": ts.trade["symbol"],
                "actual": ts.trade["actual_pnl"],
                "sim_4x": sim["pnl"],
                "diff": round(sim["pnl"] - ts.trade["actual_pnl"], 4),
                "actual_exit": ts.trade["actual_exit"],
                "sim_exit": sim["exit_reason"],
            }
        )
    fidelity_mae = (
        round(sum(abs(f["diff"]) for f in fidelity) / len(fidelity), 4) if fidelity else None
    )

    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "rules": "SL first, then TP1/TP2/TP3 scale 33/33/34, BE after TP1, then expiry at bar open_time close",
            "fee_percent": 0.1,
            "candle_preference": "market_candles DB, exchange fallback",
            "scenarios": [{"key": s["key"], "label": s["label"]} for s in SCENARIOS],
            "notes": [
                "Open trades included with data_end_mtm when still open at last candle",
                "actual_expiry_4x uses recorded expires_at when present",
                "Recorded actual PnL is the live paper ledger; replay may differ slightly (timing/fees)",
            ],
        },
        "sample": {
            "total_positions": len(trades),
            "closed": len(closed),
            "open": len(open_trades),
            "expired_exits": len(expired),
            "simulated": sum(1 for t in trade_sims if not t.skipped),
            "skipped": sum(1 for t in trade_sims if t.skipped),
            "symbols": sorted({t.symbol for t in trades}),
            "first_open": min((t.opened_at for t in trades), default=None),
            "last_open": max((t.opened_at for t in trades), default=None),
            "candle_sources": {
                "db": sum(1 for t in trade_sims if t.candle_source == "db"),
                "exchange": sum(1 for t in trade_sims if t.candle_source == "exchange"),
                "db_sparse": sum(1 for t in trade_sims if t.candle_source == "db_sparse"),
            },
        },
        "recorded_actual": {
            "closed_pnl": recorded_actual_pnl,
            "closed_n": len(closed),
            "expired_pnl": round(sum(t.actual_pnl for t in expired), 2),
            "expired_n": len(expired),
            "exit_counts": {},
        },
        "replay_fidelity": {
            "mae_vs_recorded": fidelity_mae,
            "n": len(fidelity),
            "worst": sorted(fidelity, key=lambda x: abs(x["diff"]), reverse=True)[:10],
        },
        "scenario_summary": scenario_summary,
        "help_hurt_vs_4x": dist,
        "expired_help_hurt_no_expiry": {
            "helps": help_hurt["helps"],
            "hurts": help_hurt["hurts"],
            "same": help_hurt["same"],
            "mean_delta": round(sum(help_hurt["deltas"]) / len(help_hurt["deltas"]), 2)
            if help_hurt["deltas"]
            else 0.0,
            "sum_delta": round(sum(help_hurt["deltas"]), 2) if help_hurt["deltas"] else 0.0,
        },
        "expired_trades": expired_detail,
        "all_trades": [
            {
                **ts.trade,
                "candle_count": ts.candle_count,
                "candle_source": ts.candle_source,
                "skipped": ts.skipped,
                "scenarios": {
                    k: {
                        "pnl": v["pnl"],
                        "exit": v["exit_reason"],
                        "delta": v["delta_vs_actual"],
                        "tp1": v["tp1"],
                        "tp2": v["tp2"],
                        "tp3": v["tp3"],
                    }
                    for k, v in ts.scenarios.items()
                },
            }
            for ts in trade_sims
        ],
    }

    # exit counts for recorded
    ec: dict[str, int] = {}
    for t in closed:
        k = t.actual_exit or "unknown"
        ec[k] = ec.get(k, 0) + 1
    payload["recorded_actual"]["exit_counts"] = ec

    # JSON-serialize datetimes in sample
    for k in ("first_open", "last_open"):
        v = payload["sample"][k]
        if isinstance(v, datetime):
            payload["sample"][k] = v.isoformat()

    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
