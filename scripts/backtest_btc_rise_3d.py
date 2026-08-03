"""3-day paper-style backtest: current gates WITH vs WITHOUT BTC-rise short pause.

Replays actionable DB signals through retest arm + scale-out exits (same geometry
as ``vps_gate_relax_3d.py``), applying live score bands, RR, cooldown, concurrency,
trendline retest config, and optional BTC 4h regime hard veto.

Variants:
  old — btc_rise_short_block disabled
  new — btc_rise_short_block enabled (defaults)

Usage:
  PYTHONPATH=. python scripts/backtest_btc_rise_3d.py
  PYTHONPATH=. python scripts/backtest_btc_rise_3d.py --days 3 --out exports/btc_rise_3d.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.container import build_container
from app.core.enums import SignalDirection
from app.core.logging import configure_logging, get_logger
from app.core.time import ensure_utc, timeframe_to_timedelta, utc_now
from app.database.session import session_scope
from app.indicators.engine import IndicatorEngine
from app.market_data.types import Candle
from app.models.signal import Signal
from app.repositories.asset_repository import AssetRepository
from app.repositories.signal_repository import SignalRepository
from app.signals.btc_momentum import (
    BtcRiseThresholds,
    btc_rising_short_block_reason,
    thresholds_from_settings,
)
from app.signals.regime import (
    MarketRegime,
    direction_allowed_by_regime,
    regime_from_indicators,
)
from app.signals.retest_entry import RetestEntryConfig, arm_retest_entry, levels_from_entry_sl

logger = get_logger(__name__)

RISK_USD = 50.0


@dataclass(frozen=True)
class GateSpec:
    key: str
    label: str
    btc_rise: bool
    long_min: float
    short_max: float
    short_min: float
    min_rr: float
    regime_veto: bool


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
    blocked_btc_rise: bool = False


@dataclass
class VariantStats:
    key: str
    label: str
    candidates: int = 0
    blocked_btc_rise: int = 0
    blocked_regime: int = 0
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
    long_n: int = 0
    short_n: int = 0
    long_pnl: float = 0.0
    short_pnl: float = 0.0
    exits: dict[str, int] = field(default_factory=dict)
    top_blocked_shorts: list[dict] = field(default_factory=list)
    top_trades: list[dict] = field(default_factory=list)


def _passes_score(signal: Signal, spec: GateSpec) -> bool:
    try:
        direction = SignalDirection(signal.direction)
    except ValueError:
        return False
    if not direction.is_actionable:
        return False
    score = float(signal.score)
    rr = float(signal.risk_reward_ratio or 0.0)
    if rr < spec.min_rr:
        return False
    if direction.is_long:
        return score >= spec.long_min
    return spec.short_min < score <= spec.short_max


def _candle_rows_to_candles(rows: list, hours: int) -> list[Candle]:
    out: list[Candle] = []
    for r in rows:
        ot = r[0]
        if ot.tzinfo is None:
            from datetime import UTC

            ot = ot.replace(tzinfo=UTC)
        out.append(
            Candle(
                open_time=ot,
                close_time=ot + timedelta(hours=hours),
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5] or 0),
                is_closed=bool(r[6]) if r[6] is not None else True,
            )
        )
    return out


def _closed_before(candles: list[Candle], when: datetime, hours: int) -> list[Candle]:
    when = ensure_utc(when)
    selected: list[Candle] = []
    for c in candles:
        if c.is_closed and ensure_utc(c.open_time) + timedelta(hours=hours) <= when:
            selected.append(c)
    return selected


async def _load_btc_candles(since: datetime) -> tuple[list[Candle], list[Candle]]:
    async with session_scope() as db:
        btc_id = (
            await db.execute(text("SELECT id FROM assets WHERE symbol='BTCUSDT' LIMIT 1"))
        ).scalar_one()
        start = since - timedelta(days=40)
        c1 = (
            await db.execute(
                text(
                    """
                    SELECT open_time, open, high, low, close, volume, is_closed
                    FROM market_candles
                    WHERE asset_id=:aid AND timeframe='1h' AND open_time >= :s
                    ORDER BY open_time
                    """
                ),
                {"aid": btc_id, "s": start},
            )
        ).fetchall()
        c4 = (
            await db.execute(
                text(
                    """
                    SELECT open_time, open, high, low, close, volume, is_closed
                    FROM market_candles
                    WHERE asset_id=:aid AND timeframe='4h' AND open_time >= :s
                    ORDER BY open_time
                    """
                ),
                {"aid": btc_id, "s": start},
            )
        ).fetchall()
    return _candle_rows_to_candles(list(c1), 1), _candle_rows_to_candles(list(c4), 4)


def _regime_at(
    engine: IndicatorEngine,
    candles_4h: list[Candle],
    when: datetime,
) -> MarketRegime | None:
    closed = _closed_before(candles_4h, when, 4)
    if len(closed) < 60:
        return None
    import pandas as pd

    frame = pd.DataFrame(
        {
            "open": [c.open for c in closed],
            "high": [c.high for c in closed],
            "low": [c.low for c in closed],
            "close": [c.close for c in closed],
            "volume": [c.volume for c in closed],
        },
        index=pd.DatetimeIndex([c.open_time for c in closed], name="open_time"),
    )
    try:
        indicators = engine.compute(frame, "4h", symbol="BTCUSDT", strict=False)
        snap = regime_from_indicators(indicators)
        return snap.regime if snap.available else None
    except Exception:
        return None


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
    tp_fractions: tuple[float, float, float] = (0.5, 0.25, 0.25),
) -> tuple[str, float, datetime | None, bool]:
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
            realized_r += remaining * (
                (stop - entry) / risk if is_long else (entry - stop) / risk
            )
            return "stop_loss", realized_r, when, False

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


def _stats(trades: list[SimTrade], meta: dict, spec: GateSpec) -> VariantStats:
    closed = [t for t in trades if t.status == "closed"]
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl < 0]
    gp = sum(t.pnl for t in wins)
    gl = abs(sum(t.pnl for t in losses))
    pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)
    exits: dict[str, int] = {}
    for t in closed:
        exits[t.exit_reason or "?"] = exits.get(t.exit_reason or "?", 0) + 1
    return VariantStats(
        key=spec.key,
        label=spec.label,
        candidates=int(meta["candidates"]),
        blocked_btc_rise=int(meta["blocked_btc_rise"]),
        blocked_regime=int(meta["blocked_regime"]),
        opened=int(meta["opened"]),
        retest_filled=int(meta["retest_filled"]),
        retest_skipped=int(meta["retest_skipped"]),
        md_failed=int(meta["md_failed"]),
        closed=len(closed),
        force_closed=int(meta["force_closed"]),
        total_pnl=round(sum(t.pnl for t in closed), 2),
        total_r=round(sum(t.r_multiple for t in closed), 3),
        win_rate=round(len(wins) / len(closed), 4) if closed else 0.0,
        profit_factor=round(pf, 3),
        long_n=sum(1 for t in closed if SignalDirection(t.direction).is_long),
        short_n=sum(1 for t in closed if SignalDirection(t.direction).is_short),
        long_pnl=round(
            sum(t.pnl for t in closed if SignalDirection(t.direction).is_long), 2
        ),
        short_pnl=round(
            sum(t.pnl for t in closed if SignalDirection(t.direction).is_short), 2
        ),
        exits=exits,
        top_blocked_shorts=list(meta.get("blocked_samples") or [])[:12],
        top_trades=[
            asdict(t)
            for t in sorted(closed, key=lambda x: abs(x.pnl), reverse=True)[:12]
        ],
    )


async def _simulate(
    provider,
    signals: list[Signal],
    symbols_by_id: dict[int, str],
    spec: GateSpec,
    *,
    since: datetime,
    cooldown_minutes: int,
    expiry_multiplier: int,
    retest_cfg: RetestEntryConfig,
    tp_multipliers: tuple[Decimal, Decimal, Decimal],
    tp_fractions: tuple[float, float, float],
    candle_cache: dict[tuple[str, str], list],
    btc_1h: list[Candle],
    btc_4h: list[Candle],
    rise_thresholds: BtcRiseThresholds,
    max_concurrent: int,
    indicator_engine: IndicatorEngine,
) -> VariantStats:
    cutoff = utc_now()
    lookback_pad = timedelta(days=14)
    trades: list[SimTrade] = []
    meta = {
        "candidates": 0,
        "blocked_btc_rise": 0,
        "blocked_regime": 0,
        "opened": 0,
        "retest_filled": 0,
        "retest_skipped": 0,
        "md_failed": 0,
        "force_closed": 0,
        "blocked_samples": [],
    }
    last_dispatch: dict[str, datetime] = {}
    active: list[tuple[str, datetime]] = []
    regime_cache: dict[str, MarketRegime | None] = {}

    rise_on = BtcRiseThresholds(
        enabled=spec.btc_rise,
        pct_1h=rise_thresholds.pct_1h,
        pct_3h=rise_thresholds.pct_3h,
        pct_4h=rise_thresholds.pct_4h,
        pct_6h=rise_thresholds.pct_6h,
        use_1h=rise_thresholds.use_1h,
        use_3h=rise_thresholds.use_3h,
        use_4h=rise_thresholds.use_4h,
        use_6h=rise_thresholds.use_6h,
    )

    for signal in signals:
        if not _passes_score(signal, spec):
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

        if spec.regime_veto:
            bucket = created.replace(minute=0, second=0, microsecond=0).isoformat()
            if bucket not in regime_cache:
                regime_cache[bucket] = _regime_at(indicator_engine, btc_4h, created)
            regime = regime_cache[bucket]
            if regime is not None and not direction_allowed_by_regime(regime, direction):
                meta["blocked_regime"] += 1
                continue

        if direction.is_short and rise_on.enabled:
            c1 = _closed_before(btc_1h, created, 1)
            c4 = _closed_before(btc_4h, created, 4)
            reason = btc_rising_short_block_reason(c1, c4, thresholds=rise_on)
            if reason:
                meta["blocked_btc_rise"] += 1
                if len(meta["blocked_samples"]) < 20:
                    meta["blocked_samples"].append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "score": float(signal.score),
                            "at": created.isoformat(),
                            "reason": reason,
                        }
                    )
                continue

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

        # New rule also blocks short fills while BTC is rising at fill time.
        if direction.is_short and rise_on.enabled:
            fill_time_chk = ensure_utc(arm.fill_time)
            c1 = _closed_before(btc_1h, fill_time_chk, 1)
            c4 = _closed_before(btc_4h, fill_time_chk, 4)
            if btc_rising_short_block_reason(c1, c4, thresholds=rise_on):
                meta["blocked_btc_rise"] += 1
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
            tp_fractions=tp_fractions,
        )
        if forced:
            meta["force_closed"] += 1
        free_at = closed_at or expires_at
        active.append((symbol, free_at))
        trades.append(
            SimTrade(
                symbol=symbol,
                direction=direction.value,
                score=float(signal.score),
                signal_id=int(signal.id),
                status="closed",
                exit_reason=reason,
                pnl=round(r_mult * RISK_USD, 2),
                r_multiple=round(r_mult, 3),
                opened_at=fill_time.isoformat(),
                closed_at=closed_at.isoformat() if closed_at else cutoff.isoformat(),
            )
        )

    return _stats(trades, meta, spec)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--out", default=str(ROOT / "exports" / "btc_rise_3d_backtest.json"))
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    container = build_container()
    provider = container.paper_price_provider
    settings = container.settings
    paper = container.paper_trading
    since = utc_now() - timedelta(days=args.days)
    retest_cfg = paper._retest_config()
    tp_mults = tuple(Decimal(str(m)) for m in paper._tp_multipliers)
    fracs = tuple(float(x) for x in settings.paper_scale_out_fractions.split(",") if x.strip())
    if len(fracs) != 3:
        fracs = (0.5, 0.25, 0.25)
    rise_thresholds = thresholds_from_settings(settings)
    long_min = float(settings.signal_min_score)
    short_max = float(settings.signal_short_max_score)
    short_min = float(settings.signal_short_min_score)
    min_rr = float(settings.min_risk_reward_ratio)
    regime_veto = bool(settings.regime_filter_enabled and settings.market_regime_hard_veto)
    max_open = int(settings.paper_max_open_positions)
    cooldown = int(settings.signal_cooldown_minutes)
    expiry_mult = int(settings.signal_expiry_multiplier)
    indicator_engine = IndicatorEngine(min_candles=50)

    specs = [
        GateSpec(
            key="old",
            label="Current gates WITHOUT BTC-rise short pause",
            btc_rise=False,
            long_min=long_min,
            short_max=short_max,
            short_min=short_min,
            min_rr=min_rr,
            regime_veto=regime_veto,
        ),
        GateSpec(
            key="new",
            label="Current gates WITH BTC-rise short pause",
            btc_rise=True,
            long_min=long_min,
            short_max=short_max,
            short_min=short_min,
            min_rr=min_rr,
            regime_veto=regime_veto,
        ),
    ]

    try:
        print(f"Loading signals since {since.isoformat()} ...", flush=True)
        async with session_scope() as session:
            signals = await SignalRepository(session).list_since(
                since,
                actionable_only=True,
                dispatched_only=False,
                limit=50_000,
            )
            asset_ids = list({s.asset_id for s in signals})
            symbols_by_id = await AssetRepository(session).get_symbols_by_ids(asset_ids)
            ordered = sorted(signals, key=lambda s: s.created_at)

        print(f"Actionable signals: {len(ordered)}", flush=True)
        print("Loading BTC candles ...", flush=True)
        btc_1h, btc_4h = await _load_btc_candles(since)
        print(f"BTC 1h={len(btc_1h)} 4h={len(btc_4h)}", flush=True)

        candle_cache: dict[tuple[str, str], list] = {}
        results: list[VariantStats] = []
        for spec in specs:
            print(f"sim {spec.key} ({spec.label}) ...", flush=True)
            stats = await _simulate(
                provider,
                ordered,
                symbols_by_id,
                spec,
                since=since,
                cooldown_minutes=cooldown,
                expiry_multiplier=expiry_mult,
                retest_cfg=retest_cfg,
                tp_multipliers=tp_mults,  # type: ignore[arg-type]
                tp_fractions=fracs,  # type: ignore[arg-type]
                candle_cache=candle_cache,
                btc_1h=btc_1h,
                btc_4h=btc_4h,
                rise_thresholds=rise_thresholds,
                max_concurrent=max_open,
                indicator_engine=indicator_engine,
            )
            results.append(stats)
            print(
                f"  cand={stats.candidates} rise_block={stats.blocked_btc_rise} "
                f"regime_block={stats.blocked_regime} filled={stats.retest_filled} "
                f"closed={stats.closed} pnl=${stats.total_pnl:.2f} "
                f"short_pnl=${stats.short_pnl:.2f} wr={stats.win_rate:.1%}",
                flush=True,
            )

        old = next(r for r in results if r.key == "old")
        new = next(r for r in results if r.key == "new")
        delta = round(new.total_pnl - old.total_pnl, 2)

        out = {
            "generated_at": utc_now().isoformat(),
            "days": args.days,
            "since": since.isoformat(),
            "risk_usd_per_r": RISK_USD,
            "gates": {
                "long_min": long_min,
                "short_max": short_max,
                "short_min": short_min,
                "min_rr": min_rr,
                "regime_veto": regime_veto,
                "trendline_gate": bool(settings.signal_trendline_gate_enabled),
                "btc_rise_thresholds": asdict(rise_thresholds),
                "scale_out": list(fracs),
            },
            "signals_actionable": len(ordered),
            "old": asdict(old),
            "new": asdict(new),
            "delta_pnl_usd_new_minus_old": delta,
            "delta_short_pnl_usd": round(new.short_pnl - old.short_pnl, 2),
            "delta_closed_trades": new.closed - old.closed,
        }
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")

        print("\n========== 3d BTC-rise backtest ==========")
        print(f"OLD (ohne Rising-Gate): ${old.total_pnl:.2f}  "
              f"(L ${old.long_pnl:.2f} / S ${old.short_pnl:.2f}, n={old.closed})")
        print(f"NEW (mit Rising-Gate):  ${new.total_pnl:.2f}  "
              f"(L ${new.long_pnl:.2f} / S ${new.short_pnl:.2f}, n={new.closed})")
        print(f"DIFF (NEW − OLD):       ${delta:+.2f}")
        print(f"Shorts geblockt durch Rising-Gate: {new.blocked_btc_rise}")
        print(f"Wrote {args.out}")
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
