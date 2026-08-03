"""Counterfactual: relax score gates over last N days; simulate retest+exit PnL.

Does NOT touch the live paper ledger. Pure candle math on paper_price_provider
(perp router) with the same retest arm + TP multiples as live paper.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from app.container import build_container
from app.core.enums import SignalDirection
from app.core.logging import configure_logging, get_logger
from app.core.time import ensure_utc, timeframe_to_timedelta, utc_now
from app.database.session import session_scope
from app.models.signal import Signal
from app.repositories.asset_repository import AssetRepository
from app.repositories.signal_repository import SignalRepository
from app.signals.retest_entry import RetestEntryConfig, arm_retest_entry, levels_from_entry_sl

logger = get_logger(__name__)

# Match live paper risk unit for comparable $ PnL (margin * move).
RISK_USD = 50.0


@dataclass(frozen=True)
class GateVariant:
    key: str
    label: str
    long_min: float
    short_max: float
    short_min: float = 18.0
    min_rr: float = 2.0


VARIANTS = [
    GateVariant("current", "Live: L≥75 / S≤25", 75.0, 25.0),
    GateVariant("short30", "Short≤30 / L≥75", 75.0, 30.0),
    GateVariant("short35", "Short≤35 / L≥75", 75.0, 35.0),
    GateVariant("long70_s30", "L≥70 / S≤30", 70.0, 30.0),
    GateVariant("long65_s35", "L≥65 / S≤35", 65.0, 35.0),
    GateVariant("wide", "L≥60 / S≤40", 60.0, 40.0),
]


@dataclass
class SimTrade:
    symbol: str
    direction: str
    score: float
    signal_id: int
    status: str
    exit_reason: str | None
    pnl: float
    r_multiple: float
    opened_at: str | None
    closed_at: str | None
    band: str


@dataclass
class VariantStats:
    key: str
    label: str
    long_min: float
    short_max: float
    candidates: int = 0
    opened: int = 0
    retest_filled: int = 0
    retest_skipped: int = 0
    md_failed: int = 0
    closed: int = 0
    force_closed: int = 0
    total_pnl: float = 0.0
    total_r: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_pnl: float = 0.0
    avg_r: float = 0.0
    max_dd: float = 0.0
    long_n: int = 0
    short_n: int = 0
    long_pnl: float = 0.0
    short_pnl: float = 0.0
    exits: dict[str, int] = field(default_factory=dict)
    band_pnl: dict[str, dict] = field(default_factory=dict)
    top_wins: list[dict] = field(default_factory=list)
    top_losses: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)


def _passes(signal: Signal, v: GateVariant) -> bool:
    try:
        direction = SignalDirection(signal.direction)
    except ValueError:
        return False
    if not direction.is_actionable:
        return False
    score = float(signal.score)
    rr = float(signal.risk_reward_ratio or 0.0)
    if rr < v.min_rr:
        return False
    if direction.is_long:
        return score >= v.long_min
    return v.short_min < score <= v.short_max


def _band_for(signal: Signal) -> str:
    d = SignalDirection(signal.direction)
    score = float(signal.score)
    if d.is_long:
        if score >= 75:
            return "long_75+"
        if score >= 70:
            return "long_70_75"
        if score >= 65:
            return "long_65_70"
        if score >= 60:
            return "long_60_65"
        return "long_lt60"
    if score <= 25:
        return "short_18_25"
    if score <= 30:
        return "short_25_30"
    if score <= 35:
        return "short_30_35"
    if score <= 40:
        return "short_35_40"
    return "short_gt40"


async def _get_candles_cached(
    provider,
    cache: dict[tuple[str, str], list],
    symbol: str,
    tf: str,
    *,
    start_time: datetime,
    end_time: datetime,
) -> list:
    key = (symbol.upper(), tf)
    if key in cache:
        return cache[key]
    try:
        series = await provider.get_candles(
            symbol,
            tf,
            limit=5_000,
            start_time=start_time,
            end_time=end_time,
        )
        candles = list(series.candles) if series and not series.is_empty else []
    except Exception as exc:
        logger.warning("sim_candles_failed", symbol=symbol, tf=tf, error=str(exc))
        candles = []
    cache[key] = candles
    return candles


def _replay_exit(
    *,
    is_long: bool,
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    tp3: float,
    bars: list,
    expires_at: datetime,
    cutoff: datetime,
    tp_fractions: tuple[float, float, float] = (0.4, 0.35, 0.25),
) -> tuple[str, float, datetime | None, bool]:
    """Return exit_reason, R-multiple, closed_at, force_closed.

    Partial TP scale-out approximated with live-like fractions; remainder to SL/expiry.
    """
    risk = abs(entry - stop)
    if risk <= 0 or not bars:
        return "no_bars", 0.0, None, True

    remaining = 1.0
    realized_r = 0.0
    frac1, frac2, frac3 = tp_fractions
    hit1 = hit2 = hit3 = False
    closed_at: datetime | None = None

    for c in bars:
        when = ensure_utc(c.open_time)
        if when > cutoff:
            break
        high = float(c.high)
        low = float(c.low)
        close = float(c.close)

        stop_hit = low <= stop if is_long else high >= stop
        if stop_hit:
            realized_r += remaining * ((stop - entry) / risk if is_long else (entry - stop) / risk)
            return "stop_loss", realized_r, when, False

        # TPs in order (favourable excursion)
        if is_long:
            if not hit1 and high >= tp1:
                realized_r += frac1 * ((tp1 - entry) / risk)
                remaining -= frac1
                hit1 = True
            if not hit2 and remaining > 0 and high >= tp2:
                realized_r += frac2 * ((tp2 - entry) / risk)
                remaining -= frac2
                hit2 = True
            if not hit3 and remaining > 0 and high >= tp3:
                realized_r += frac3 * ((tp3 - entry) / risk)
                remaining -= frac3
                hit3 = True
                return "take_profit_3", realized_r, when, False
        else:
            if not hit1 and low <= tp1:
                realized_r += frac1 * ((entry - tp1) / risk)
                remaining -= frac1
                hit1 = True
            if not hit2 and remaining > 0 and low <= tp2:
                realized_r += frac2 * ((entry - tp2) / risk)
                remaining -= frac2
                hit2 = True
            if not hit3 and remaining > 0 and low <= tp3:
                realized_r += frac3 * ((entry - tp3) / risk)
                remaining -= frac3
                hit3 = True
                return "take_profit_3", realized_r, when, False

        if when >= expires_at and remaining > 0:
            move = (close - entry) / risk if is_long else (entry - close) / risk
            realized_r += remaining * move
            return "expired", realized_r, when, False

        closed_at = when

    # force at cutoff mark
    if remaining > 0 and bars:
        last = None
        for c in reversed(bars):
            if ensure_utc(c.open_time) <= cutoff:
                last = c
                break
        last = last or bars[-1]
        close = float(last.close)
        move = (close - entry) / risk if is_long else (entry - close) / risk
        realized_r += remaining * move
        return "force_cutoff", realized_r, ensure_utc(last.open_time), True

    return "force_cutoff", realized_r, closed_at, True


def _stats_from_trades(trades: list[SimTrade], v: GateVariant, meta: dict) -> VariantStats:
    closed = [t for t in trades if t.status == "closed"]
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl < 0]
    gp = sum(t.pnl for t in wins)
    gl = abs(sum(t.pnl for t in losses))
    pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)
    total = sum(t.pnl for t in closed)
    total_r = sum(t.r_multiple for t in closed)

    ordered = sorted(closed, key=lambda t: t.closed_at or "")
    eq = 5000.0
    peak = eq
    max_dd = 0.0
    curve = [{"t": "start", "equity": eq}]
    for t in ordered:
        eq += t.pnl
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
        curve.append({"t": t.closed_at, "equity": round(eq, 2), "symbol": t.symbol})

    exits: dict[str, int] = {}
    for t in closed:
        exits[t.exit_reason or "unknown"] = exits.get(t.exit_reason or "unknown", 0) + 1

    band_pnl: dict[str, dict] = {}
    for t in closed:
        b = band_pnl.setdefault(t.band, {"n": 0, "pnl": 0.0, "wins": 0, "r": 0.0})
        b["n"] += 1
        b["pnl"] = round(b["pnl"] + t.pnl, 2)
        b["r"] = round(b["r"] + t.r_multiple, 3)
        if t.pnl > 0:
            b["wins"] += 1
    for b in band_pnl.values():
        b["wr"] = round(b["wins"] / b["n"], 4) if b["n"] else 0.0
        b["avg_r"] = round(b["r"] / b["n"], 3) if b["n"] else 0.0

    top_wins = sorted(closed, key=lambda t: t.pnl, reverse=True)[:8]
    top_losses = sorted(closed, key=lambda t: t.pnl)[:8]

    return VariantStats(
        key=v.key,
        label=v.label,
        long_min=v.long_min,
        short_max=v.short_max,
        candidates=int(meta.get("candidates", 0)),
        opened=int(meta.get("opened", 0)),
        retest_filled=int(meta.get("retest_filled", 0)),
        retest_skipped=int(meta.get("retest_skipped", 0)),
        md_failed=int(meta.get("md_failed", 0)),
        closed=len(closed),
        force_closed=int(meta.get("force_closed", 0)),
        total_pnl=round(total, 2),
        total_r=round(total_r, 3),
        win_rate=round(len(wins) / len(closed), 4) if closed else 0.0,
        profit_factor=round(pf, 3),
        avg_pnl=round(total / len(closed), 2) if closed else 0.0,
        avg_r=round(total_r / len(closed), 3) if closed else 0.0,
        max_dd=round(max_dd, 2),
        long_n=sum(1 for t in closed if SignalDirection(t.direction).is_long),
        short_n=sum(1 for t in closed if SignalDirection(t.direction).is_short),
        long_pnl=round(sum(t.pnl for t in closed if SignalDirection(t.direction).is_long), 2),
        short_pnl=round(sum(t.pnl for t in closed if SignalDirection(t.direction).is_short), 2),
        exits=exits,
        band_pnl=band_pnl,
        top_wins=[asdict(t) for t in top_wins],
        top_losses=[asdict(t) for t in top_losses],
        equity_curve=curve[-80:],
    )


async def _simulate_variant(
    provider,
    signals: list[Signal],
    symbols_by_id: dict[int, str],
    v: GateVariant,
    *,
    since: datetime,
    cooldown_minutes: int,
    expiry_multiplier: int,
    retest_cfg: RetestEntryConfig,
    tp_multipliers: tuple[Decimal, Decimal, Decimal],
    candle_cache: dict[tuple[str, str], list],
    max_concurrent: int = 20,
) -> VariantStats:
    """Portfolio-aware: max concurrent filled trades; cooldown per symbol."""
    cutoff = utc_now()
    lookback_pad = timedelta(days=14)
    trades: list[SimTrade] = []
    meta = {
        "candidates": 0,
        "opened": 0,
        "retest_filled": 0,
        "retest_skipped": 0,
        "md_failed": 0,
        "force_closed": 0,
    }
    last_dispatch: dict[str, datetime] = {}
    # active: (symbol, free_at) — freed at trade close
    active: list[tuple[str, datetime]] = []

    for signal in signals:
        if not _passes(signal, v):
            continue
        meta["candidates"] += 1
        symbol = symbols_by_id.get(signal.asset_id)
        if not symbol:
            continue
        symbol = symbol.upper()
        created = ensure_utc(signal.created_at)
        direction = SignalDirection(signal.direction)
        is_long = direction.is_long
        tf = signal.primary_timeframe or "1h"
        band = _band_for(signal)

        prev = last_dispatch.get(symbol)
        if prev and (created - prev).total_seconds() < cooldown_minutes * 60:
            continue

        active = [(s, free) for (s, free) in active if free > created]
        if any(s == symbol for s, _ in active):
            continue
        if len(active) >= max_concurrent:
            continue

        meta["opened"] += 1
        last_dispatch[symbol] = created

        if signal.stop_loss is None:
            meta["retest_skipped"] += 1
            continue
        lo = float(signal.entry_low or signal.reference_price)
        hi = float(signal.entry_high or signal.reference_price)
        ref_entry = (lo + hi) / 2.0
        orig_sl = float(signal.stop_loss)

        candles = await _get_candles_cached(
            provider,
            candle_cache,
            symbol,
            tf,
            start_time=since - lookback_pad,
            end_time=cutoff,
        )
        if not candles:
            meta["md_failed"] += 1
            meta["retest_skipped"] += 1
            continue

        arm = arm_retest_entry(
            direction=direction,
            arm_time=created,
            reference_entry=ref_entry,
            original_stop=orig_sl,
            timeframe=tf,
            candles=candles,
            config=retest_cfg,
        )
        if not (
            arm.filled
            and arm.fill_price is not None
            and arm.fill_time is not None
            and arm.stop is not None
        ):
            meta["retest_skipped"] += 1
            continue

        meta["retest_filled"] += 1
        entry = float(arm.fill_price)
        stop = float(arm.stop)
        fill_time = ensure_utc(arm.fill_time)
        tp1, tp2, tp3 = levels_from_entry_sl(
            Decimal(str(entry)),
            Decimal(str(stop)),
            is_long=is_long,
            multipliers=tp_multipliers,
        )
        expires_at = fill_time + expiry_multiplier * timeframe_to_timedelta(tf)

        bars = [c for c in candles if ensure_utc(c.open_time) >= fill_time]
        reason, r_mult, closed_at, forced = _replay_exit(
            is_long=is_long,
            entry=entry,
            stop=stop,
            tp1=float(tp1),
            tp2=float(tp2),
            tp3=float(tp3),
            bars=bars,
            expires_at=expires_at,
            cutoff=cutoff,
        )
        if forced:
            meta["force_closed"] += 1

        free_at = closed_at or expires_at
        active.append((symbol, free_at))

        pnl = round(r_mult * RISK_USD, 2)
        trades.append(
            SimTrade(
                symbol=symbol,
                direction=direction.value,
                score=float(signal.score),
                signal_id=int(signal.id),
                status="closed",
                exit_reason=reason,
                pnl=pnl,
                r_multiple=round(r_mult, 3),
                opened_at=fill_time.isoformat(),
                closed_at=closed_at.isoformat() if closed_at else cutoff.isoformat(),
                band=band,
            )
        )

    return _stats_from_trades(trades, v, meta)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--out", default="/tmp/gate_relax_3d.json")
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    container = build_container()
    provider = container.paper_price_provider
    settings = container.settings
    since = utc_now() - timedelta(days=args.days)
    cooldown = int(settings.signal_cooldown_minutes)
    expiry_mult = int(settings.signal_expiry_multiplier)
    paper = container.paper_trading
    retest_cfg = paper._retest_config()
    tp_mults = tuple(Decimal(str(m)) for m in paper._tp_multipliers)
    max_open = int(settings.paper_max_open_positions)

    try:
        async with session_scope() as session:
            signals = await SignalRepository(session).list_since(
                since,
                actionable_only=True,
                dispatched_only=False,
                limit=20_000,
            )
            asset_ids = list({s.asset_id for s in signals})
            symbols_by_id = await AssetRepository(session).get_symbols_by_ids(asset_ids)
            ordered = sorted(signals, key=lambda s: s.created_at)

            funnel = {
                "signals_actionable": len(ordered),
                "since": since.isoformat(),
                "days": args.days,
                "risk_usd_per_trade": RISK_USD,
                "max_concurrent": max_open,
                "live_gates": {"long_min": 75.0, "short_max": 25.0, "short_min": 18.0},
                "by_variant_candidates": {},
                "score_hist_short": {},
                "score_hist_long": {},
                "bands": {},
            }
            for s in ordered:
                d = SignalDirection(s.direction)
                score = float(s.score)
                bucket = int(score // 5) * 5
                key = "score_hist_long" if d.is_long else "score_hist_short"
                funnel[key][str(bucket)] = funnel[key].get(str(bucket), 0) + 1
                band = _band_for(s)
                funnel["bands"][band] = funnel["bands"].get(band, 0) + 1
            for v in VARIANTS:
                funnel["by_variant_candidates"][v.key] = sum(
                    1 for s in ordered if _passes(s, v)
                )

        results: list[VariantStats] = []
        candle_cache: dict[tuple[str, str], list] = {}
        for v in VARIANTS:
            print(f"sim {v.key} ...", flush=True)
            stats = await _simulate_variant(
                provider,
                ordered,
                symbols_by_id,
                v,
                since=since,
                cooldown_minutes=cooldown,
                expiry_multiplier=expiry_mult,
                retest_cfg=retest_cfg,
                tp_multipliers=tp_mults,  # type: ignore[arg-type]
                candle_cache=candle_cache,
                max_concurrent=max_open,
            )
            results.append(stats)
            print(
                f"  cand={stats.candidates} opened={stats.opened} filled={stats.retest_filled} "
                f"closed={stats.closed} pnl=${stats.total_pnl} R={stats.total_r} "
                f"wr={stats.win_rate:.1%} pf={stats.profit_factor}",
                flush=True,
            )

        baseline = next(r for r in results if r.key == "current")
        out = {
            "generated_at": utc_now().isoformat(),
            "funnel": funnel,
            "baseline_key": "current",
            "variants": [],
        }
        for r in results:
            d = asdict(r)
            d["delta_pnl_vs_current"] = round(r.total_pnl - baseline.total_pnl, 2)
            d["delta_n_vs_current"] = r.closed - baseline.closed
            d["delta_pf_vs_current"] = round(r.profit_factor - baseline.profit_factor, 3)
            d["delta_r_vs_current"] = round(r.total_r - baseline.total_r, 3)
            out["variants"].append(d)

        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print(f"WROTE {args.out}")
        print(
            json.dumps(
                {
                    "funnel": funnel["by_variant_candidates"],
                    "bands": funnel["bands"],
                    "pnls": {r.key: r.total_pnl for r in results},
                    "pfs": {r.key: r.profit_factor for r in results},
                    "closed": {r.key: r.closed for r in results},
                },
                indent=2,
            )
        )
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
