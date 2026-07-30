"""Historical A/B backtest: baseline IST vs retest / retest+ADX / delay+30m.

Regenerates STRONG signals on Top-N universe candles (DB preferred, same gates
as live), then replays four entry arms with paper economics ($100×10x, 0.1% fee,
TP 2/4/6R scale 33/33/34, BE after TP1, expiry 4× TF).

Does not change live config. Writes JSON to --out (or stdout).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.backtesting.engine import WARMUP_CANDLES, BacktestConfig, BacktestEngine
from app.container import build_container
from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.errors import BacktestError
from app.core.logging import configure_logging
from app.core.time import ensure_utc, timeframe_to_timedelta, utc_now
from app.database.session import session_scope
from app.market_data.types import Candle
from app.models.market import Asset
from app.repositories.asset_repository import AssetRepository
from app.repositories.strategy_repository import StrategyRepository
from app.signals.risk import DEFAULT_TP_MULTIPLIERS
from app.strategies.weights import DEFAULT_WEIGHTS

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
class SignalCandidate:
    id: int
    symbol: str
    rank: int
    direction: str
    timeframe: str
    signal_at: datetime
    opened_at: datetime
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    score: float
    adx: float | None
    market_phase: str | None
    expires_at: datetime
    candles_1h: int


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
    symbol: str | None = None


def _tf_delta(tf: str) -> timedelta:
    return timedelta(seconds=TF_SECONDS.get(tf, 3600))


def _is_long(direction: str) -> bool:
    return SignalDirection(direction).is_long


def _index_time(df: pd.DataFrame, position: int) -> datetime:
    value = df.index[position]
    return value if isinstance(value, datetime) else pd.Timestamp(value).to_pydatetime()


def _candles_from_df(df: pd.DataFrame, timeframe: str) -> list[Candle]:
    interval = timeframe_to_timedelta(timeframe)
    out: list[Candle] = []
    for ts, row in df.iterrows():
        open_time = ensure_utc(ts if isinstance(ts, datetime) else pd.Timestamp(ts).to_pydatetime())
        out.append(
            Candle(
                open_time=open_time,
                close_time=open_time + interval,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                quote_volume=float(row["quote_volume"]) if "quote_volume" in row and pd.notna(row["quote_volume"]) else None,
                trade_count=int(row["trade_count"]) if "trade_count" in row and pd.notna(row.get("trade_count")) else None,
                is_closed=True,
            )
        )
    return out


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


def _levels_from_entry_sl(entry: Decimal, stop: Decimal, is_long: bool) -> tuple[Decimal, Decimal, Decimal]:
    risk = abs(entry - stop)
    if risk <= 0:
        risk = entry * Decimal("0.01")
    direction = Decimal("1") if is_long else Decimal("-1")
    tps = tuple(entry + direction * m * risk for m in TP_MULTS)
    return tps[0], tps[1], tps[2]


def _arm_retest(
    *,
    direction: str,
    signal_at: datetime,
    reference_entry: float,
    stop_loss: float,
    timeframe: str,
    candles: list[Candle],
) -> tuple[float | None, datetime | None, str | None]:
    is_long = _is_long(direction)
    arm_time = ensure_utc(signal_at)
    reference = Decimal(str(reference_entry))
    stop = Decimal(str(stop_loss))
    pending_until = arm_time + PENDING_MULT * _tf_delta(timeframe)

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
    *,
    direction: str,
    timeframe: str,
    candles: list[Candle],
    expires_at: datetime | None,
    entry: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
    tp3: float,
    opened_at: datetime,
    symbol: str | None = None,
) -> ReplayResult:
    is_long = _is_long(direction)
    open_at = ensure_utc(opened_at)
    entry_d = Decimal(str(entry))
    current_stop = Decimal(str(stop_loss))
    tp1_d = Decimal(str(tp1))
    tp2_d = Decimal(str(tp2))
    tp3_d = Decimal(str(tp3))

    notional_d = MARGIN * LEVERAGE
    qty0 = notional_d / entry_d if entry_d > 0 else Decimal("0")
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

    def reduce(
        price: Decimal,
        fraction: Decimal | None,
        reason: str,
        when: datetime,
        *,
        all_rest: bool = False,
    ) -> None:
        nonlocal rem, realized, fees, exit_reason, closed, closed_at_sim
        if rem <= 0:
            return
        q = rem if all_rest or fraction is None else min(qty0 * fraction, rem)
        if q <= 0:
            return
        direction_sign = Decimal("1") if is_long else Decimal("-1")
        gross = (price - entry_d) * q * direction_sign
        fee = price * q * FEE
        rem -= q
        realized += gross - fee
        fees += fee
        exit_reason = reason
        closed_at_sim = when
        if rem <= Decimal("0.00000001"):
            rem = Decimal("0")
            closed = True

    exp = ensure_utc(expires_at) if expires_at else None

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

        if not tp1_hit:
            hit = fav >= tp1_d if is_long else fav <= tp1_d
            if hit:
                reduce(tp1_d, SCALE[0], "take_profit_1", when)
                tp1_hit = True
                if MOVE_STOP_TO_BE:
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
        hold_hours = bars * TF_SECONDS.get(timeframe, 3600) / 3600.0

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
        entry=entry,
        opened_at=open_at.isoformat(),
        closed_at_sim=closed_at_sim.isoformat() if closed_at_sim else None,
        symbol=symbol,
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


def _symbol_pnl(rows: list[ReplayResult]) -> dict[str, float]:
    by_sym: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.skipped is None and r.symbol:
            by_sym[r.symbol] += r.pnl
    return {k: round(v, 2) for k, v in by_sym.items()}


async def _load_top_symbols(limit: int) -> list[tuple[str, int]]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Asset.symbol, Asset.market_cap_rank)
                .where(
                    Asset.in_universe.is_(True),
                    Asset.is_active.is_(True),
                    Asset.market_cap_rank.is_not(None),
                )
                .order_by(Asset.market_cap_rank.asc())
                .limit(limit)
            )
        ).all()
    return [(str(symbol).upper(), int(rank)) for symbol, rank in rows]


async def _load_mtf_frames(
    session: Any,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    timeframes: tuple[str, ...],
    *,
    use_mtf: bool,
) -> tuple[dict[str, pd.DataFrame], int]:
    tfs = list(timeframes) if use_mtf else [timeframe]
    frames: dict[str, pd.DataFrame] = {}
    candles_loaded = 0
    repo = AssetRepository(session)
    for tf in tfs:
        warmup_start = ensure_utc(start) - timeframe_to_timedelta(tf) * WARMUP_CANDLES
        series = await repo.load_candle_series(
            symbol,
            tf,
            start_time=warmup_start,
            end_time=ensure_utc(end),
            limit=100_000,
        )
        if series.is_empty:
            if tf == timeframe:
                raise BacktestError(f"No candles for {symbol} {tf}")
            continue
        min_bars = WARMUP_CANDLES + 10
        if tf != timeframe and len(series) < min_bars:
            continue
        frames[tf] = series.to_dataframe()
        candles_loaded += len(series)
    if timeframe not in frames:
        raise BacktestError(f"Primary TF missing for {symbol} {timeframe}")
    return frames, candles_loaded


def _harvest_signals(
    *,
    engine: BacktestEngine,
    config: BacktestConfig,
    mtf_frames: dict[str, pd.DataFrame],
    symbol: str,
    rank: int,
    start: datetime,
    end: datetime,
    next_id: int,
) -> tuple[list[SignalCandidate], int]:
    primary_tf = config.timeframe
    primary_df = mtf_frames[primary_tf]
    candles_list = _candles_from_df(primary_df, primary_tf)
    start_utc = ensure_utc(start)
    end_utc = ensure_utc(end)
    last_entry_at: datetime | None = None
    candidates: list[SignalCandidate] = []
    total = len(primary_df)
    evaluated = 0

    for i in range(WARMUP_CANDLES, total - 2):
        candle_time = ensure_utc(_index_time(primary_df, i))
        if candle_time < start_utc or candle_time > end_utc:
            continue
        evaluated += 1

        if config.use_multi_timeframe and len(mtf_frames) > 1:
            signal = engine._generate_signal_mtf(mtf_frames, candle_time, i)  # noqa: SLF001
        else:
            window = primary_df.iloc[: i + 1]
            signal = engine._generate_signal(window, i)  # noqa: SLF001

        if signal is None:
            continue
        if signal.direction is SignalDirection.NO_TRADE or signal.no_trade_reason:
            continue
        if not signal.direction.is_actionable:
            continue
        if config.require_strong_signals and signal.direction not in {
            SignalDirection.STRONG_LONG,
            SignalDirection.STRONG_SHORT,
        }:
            continue
        if signal.direction.is_long and signal.score < config.min_score:
            continue
        if signal.direction.is_short and (100.0 - signal.score) < config.min_score:
            continue
        if signal.risk is None:
            continue

        entry_index = i + 1
        if entry_index >= total:
            continue
        opened_at = ensure_utc(_index_time(primary_df, entry_index))
        if opened_at > end_utc + _tf_delta(primary_tf):
            continue

        if config.cooldown_minutes > 0 and last_entry_at is not None:
            elapsed = (candle_time - last_entry_at).total_seconds() / 60.0
            if elapsed < config.cooldown_minutes:
                continue

        primary = signal.assessments.get(primary_tf)
        adx = None
        if primary is not None and primary.indicators.adx_14 is not None:
            adx = float(primary.indicators.adx_14)

        entry = float(primary_df["open"].iloc[entry_index])
        risk = signal.risk
        candidates.append(
            SignalCandidate(
                id=next_id + len(candidates),
                symbol=symbol,
                rank=rank,
                direction=signal.direction.value,
                timeframe=primary_tf,
                signal_at=candle_time,
                opened_at=opened_at,
                entry=entry,
                stop_loss=float(risk.stop_loss),
                tp1=float(risk.take_profit_1),
                tp2=float(risk.take_profit_2),
                tp3=float(risk.take_profit_3),
                score=float(signal.score),
                adx=adx,
                market_phase=signal.market_phase.value if signal.market_phase else None,
                expires_at=ensure_utc(signal.expires_at),
                candles_1h=len(primary_df),
            )
        )
        last_entry_at = opened_at

    return candidates, evaluated


def _run_arm_baseline(sig: SignalCandidate, candles: list[Candle]) -> ReplayResult:
    if not any(ensure_utc(c.open_time) >= ensure_utc(sig.opened_at) for c in candles):
        return ReplayResult(pnl=0.0, fees=0.0, exit_reason="skipped", skipped="no_candles", symbol=sig.symbol)
    return _simulate(
        direction=sig.direction,
        timeframe=sig.timeframe,
        candles=candles,
        expires_at=sig.expires_at,
        entry=sig.entry,
        stop_loss=sig.stop_loss,
        tp1=sig.tp1,
        tp2=sig.tp2,
        tp3=sig.tp3,
        opened_at=sig.opened_at,
        symbol=sig.symbol,
    )


def _run_arm_retest(sig: SignalCandidate, candles: list[Candle]) -> ReplayResult:
    fill, fill_time, reason = _arm_retest(
        direction=sig.direction,
        signal_at=sig.signal_at,
        reference_entry=sig.entry,
        stop_loss=sig.stop_loss,
        timeframe=sig.timeframe,
        candles=candles,
    )
    if fill is None or fill_time is None:
        return ReplayResult(
            pnl=0.0, fees=0.0, exit_reason="skipped", skipped=reason or "no_fill", symbol=sig.symbol
        )
    is_long = _is_long(sig.direction)
    entry_d = Decimal(str(fill))
    old_risk = abs(Decimal(str(sig.entry)) - Decimal(str(sig.stop_loss)))
    stop_d = entry_d - old_risk if is_long else entry_d + old_risk
    tp1_d, tp2_d, tp3_d = _levels_from_entry_sl(entry_d, stop_d, is_long)
    return _simulate(
        direction=sig.direction,
        timeframe=sig.timeframe,
        candles=candles,
        expires_at=sig.expires_at,
        entry=fill,
        stop_loss=float(stop_d),
        tp1=float(tp1_d),
        tp2=float(tp2_d),
        tp3=float(tp3_d),
        opened_at=fill_time,
        symbol=sig.symbol,
    )


def _run_arm_delay(sig: SignalCandidate, candles: list[Candle]) -> ReplayResult:
    delay_at = ensure_utc(sig.opened_at) + timedelta(minutes=30)
    is_long = _is_long(sig.direction)
    stop = Decimal(str(sig.stop_loss))
    invalidated = False
    entry_px: float | None = None
    for c in candles:
        when = ensure_utc(c.open_time)
        if when < ensure_utc(sig.opened_at):
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
        return ReplayResult(
            pnl=0.0,
            fees=0.0,
            exit_reason="skipped",
            skipped="sl_before_delay" if invalidated else "no_bar_after_delay",
            symbol=sig.symbol,
        )
    return _simulate(
        direction=sig.direction,
        timeframe=sig.timeframe,
        candles=candles,
        expires_at=sig.expires_at,
        entry=entry_px,
        stop_loss=sig.stop_loss,
        tp1=sig.tp1,
        tp2=sig.tp2,
        tp3=sig.tp3,
        opened_at=delay_at,
        symbol=sig.symbol,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Retest-variants historical backtest")
    parser.add_argument("--top", type=int, default=100)
    parser.add_argument("--days", type=int, default=42)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--no-mtf", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument("--progress-every", type=int, default=1)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    end = utc_now()
    start = end - timedelta(days=args.days)
    symbols = await _load_top_symbols(args.top)
    if not symbols:
        print("No universe symbols found", file=sys.stderr)
        await container.aclose()
        return 1

    use_mtf = not args.no_mtf
    print(
        f"Retest-variants BT · top={len(symbols)} · {args.days}d · {args.timeframe} · "
        f"mtf={use_mtf} · {start.date()} → {end.date()}",
        file=sys.stderr,
        flush=True,
    )

    all_signals: list[SignalCandidate] = []
    candle_cache: dict[str, list[Candle]] = {}
    symbol_errors: list[dict[str, Any]] = []
    symbols_ok = 0
    candles_evaluated = 0
    next_id = 1

    try:
        async with session_scope() as session:
            weights = DEFAULT_WEIGHTS
            loaded, _ = await StrategyRepository(session).load_weights()
            if loaded is not None:
                weights = loaded

            for idx, (symbol, rank) in enumerate(symbols, start=1):
                if idx % max(1, args.progress_every) == 0 or idx == 1:
                    print(f"[{idx}/{len(symbols)}] #{rank} {symbol} ...", file=sys.stderr, flush=True)
                try:
                    config = BacktestConfig.from_settings(
                        settings,
                        symbol=symbol,
                        timeframe=args.timeframe,
                        weights=weights,
                        fee_percent=0.1,
                        slippage_percent=0.0,
                        initial_capital=10_000.0,
                        use_multi_timeframe=use_mtf,
                    )
                    frames, loaded_n = await _load_mtf_frames(
                        session,
                        symbol,
                        args.timeframe,
                        start,
                        end,
                        config.timeframes,
                        use_mtf=use_mtf,
                    )
                    engine = BacktestEngine(config)
                    cands, evaluated = _harvest_signals(
                        engine=engine,
                        config=config,
                        mtf_frames=frames,
                        symbol=symbol,
                        rank=rank,
                        start=start,
                        end=end,
                        next_id=next_id,
                    )
                    next_id += len(cands)
                    all_signals.extend(cands)
                    candle_cache[symbol] = _candles_from_df(frames[args.timeframe], args.timeframe)
                    symbols_ok += 1
                    candles_evaluated += evaluated
                    print(
                        f"  signals={len(cands)} candles_loaded={loaded_n} evaluated={evaluated}",
                        file=sys.stderr,
                        flush=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  FAILED: {exc}", file=sys.stderr, flush=True)
                    symbol_errors.append({"symbol": symbol, "rank": rank, "error": str(exc)})
    finally:
        await container.aclose()

    # --- Arms ---
    arms_spec = [
        ("A_baseline_ist", "Baseline IST (next 1h open)"),
        ("B_retest", "Retest / pullback entry"),
        ("C_retest_adx30", "Retest + ADX ≥ 30"),
        ("D_delay_30m", "Delay entry +30m"),
    ]

    arm_rows: dict[str, list[ReplayResult]] = {k: [] for k, _ in arms_spec}

    for sig in all_signals:
        candles = candle_cache.get(sig.symbol, [])
        arm_rows["A_baseline_ist"].append(_run_arm_baseline(sig, candles))
        arm_rows["B_retest"].append(_run_arm_retest(sig, candles))
        if sig.adx is None or sig.adx < 30:
            arm_rows["C_retest_adx30"].append(
                ReplayResult(
                    pnl=0.0,
                    fees=0.0,
                    exit_reason="skipped",
                    skipped="adx_below_30" if sig.adx is not None else "no_adx",
                    symbol=sig.symbol,
                )
            )
        else:
            arm_rows["C_retest_adx30"].append(_run_arm_retest(sig, candles))
        arm_rows["D_delay_30m"].append(_run_arm_delay(sig, candles))

    baseline_agg = _agg(arm_rows["A_baseline_ist"])
    baseline_pnl = float(baseline_agg["total_pnl"])

    arms_out: list[dict[str, Any]] = []
    per_symbol: dict[str, dict[str, float]] = {}
    for key, label in arms_spec:
        agg = _agg(arm_rows[key], baseline_pnl=baseline_pnl)
        sym_pnl = _symbol_pnl(arm_rows[key])
        per_symbol[key] = sym_pnl
        top_winners = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)[:10]
        top_losers = sorted(sym_pnl.items(), key=lambda x: x[1])[:10]
        # Concentration: share of |PnL| from top absolute contributors
        abs_sorted = sorted(sym_pnl.items(), key=lambda x: abs(x[1]), reverse=True)
        total_abs = sum(abs(v) for _, v in abs_sorted) or 1.0
        top1_share = abs(abs_sorted[0][1]) / total_abs if abs_sorted else 0.0
        arms_out.append(
            {
                "key": key,
                "label": label,
                **agg,
                "top_winners": [{"symbol": s, "pnl": p} for s, p in top_winners if p > 0],
                "top_losers": [{"symbol": s, "pnl": p} for s, p in top_losers if p < 0],
                "top1_abs_pnl_share": round(top1_share, 4),
                "top1_symbol": abs_sorted[0][0] if abs_sorted else None,
            }
        )

    # Pairwise: retest vs baseline per signal
    improved = worsened = same = 0
    for a, b in zip(arm_rows["A_baseline_ist"], arm_rows["B_retest"], strict=False):
        ap = 0.0 if a.skipped else a.pnl
        bp = 0.0 if b.skipped else b.pnl
        if bp > ap + 1e-9:
            improved += 1
        elif bp < ap - 1e-9:
            worsened += 1
        else:
            same += 1

    longs = sum(1 for s in all_signals if _is_long(s.direction))
    shorts = len(all_signals) - longs
    with_adx = sum(1 for s in all_signals if s.adx is not None)
    adx_ge_30 = sum(1 for s in all_signals if s.adx is not None and s.adx >= 30)

    # Verdict
    by_key = {a["key"]: a for a in arms_out}
    retest = by_key["B_retest"]
    base = by_key["A_baseline_ist"]
    retest_adx = by_key["C_retest_adx30"]
    delay = by_key["D_delay_30m"]

    def _beats(arm: dict[str, Any]) -> bool:
        return (
            arm["total_pnl"] > base["total_pnl"]
            and arm["profit_factor"] >= 1.0
            and arm["n"] >= max(20, int(0.25 * max(base["n"], 1)))
        )

    if _beats(retest) and retest["total_pnl"] >= retest_adx["total_pnl"] and retest["total_pnl"] >= delay["total_pnl"]:
        verdict = "SUPPORTS"
        deploy = "B_retest"
        verdict_note = "Retest beats baseline on PnL/PF with adequate fills; best among arms."
    elif _beats(retest_adx) and retest_adx["total_pnl"] > retest["total_pnl"]:
        verdict = "SUPPORTS"
        deploy = "C_retest_adx30"
        verdict_note = "Retest+ADX≥30 is the strongest arm vs baseline."
    elif _beats(delay) and delay["total_pnl"] > retest["total_pnl"]:
        verdict = "MIXED"
        deploy = "D_delay_30m"
        verdict_note = "Delay+30m beats baseline; retest weaker — do not promote retest alone."
    elif retest["total_pnl"] > base["total_pnl"] and retest["profit_factor"] < 1.0:
        verdict = "MIXED"
        deploy = None
        verdict_note = "Retest improves PnL vs baseline but PF still < 1 — not deploy-ready."
    elif retest["total_pnl"] <= base["total_pnl"]:
        verdict = "REJECTS"
        deploy = None
        verdict_note = "Retest does not beat baseline IST on this sample."
    else:
        verdict = "MIXED"
        deploy = None
        verdict_note = "Inconclusive — check fill rate / outliers."

    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "universe": f"top-{args.top} in_universe by market_cap_rank",
            "period_days": args.days,
            "timeframe": args.timeframe,
            "use_multi_timeframe": use_mtf,
            "candle_source": "market_candles DB",
            "signal_regen": "BacktestEngine gates (STRONG / score / ADX min / RR / cooldown)",
            "economics": "$100 margin × 10x, fee 0.1%, scale 33/33/34, BE after TP1, expiry 4× TF",
            "arms": [label for _, label in arms_spec],
            "notes": [
                "Signals regenerated on historical candles (not live paper ledger).",
                "15m MTF coverage may be sparse for older dates; secondary TFs skipped if short.",
                "Skipped arm fills contribute $0 (not taken), matching paper sweep accounting.",
            ],
        },
        "gates": {
            "min_score": settings.signal_min_score,
            "short_max_score": settings.signal_short_max_score,
            "require_strong": settings.signal_require_strong,
            "min_adx": settings.signal_min_adx,
            "min_rr": settings.min_risk_reward_ratio,
            "cooldown_minutes": settings.signal_cooldown_minutes,
            "expiry_multiplier": settings.signal_expiry_multiplier,
            "block_range_market": settings.signal_block_range_market,
        },
        "sample": {
            "top_n": args.top,
            "days": args.days,
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "symbols_ok": symbols_ok,
            "symbols_failed": len(symbol_errors),
            "candles_evaluated": candles_evaluated,
            "signals": len(all_signals),
            "longs": longs,
            "shorts": shorts,
            "with_adx": with_adx,
            "adx_ge_30": adx_ge_30,
            "first_signal": min((s.signal_at for s in all_signals), default=None),
            "last_signal": max((s.signal_at for s in all_signals), default=None),
        },
        "arms": arms_out,
        "retest_vs_baseline_signal_pairs": {
            "improved": improved,
            "worsened": worsened,
            "same": same,
        },
        "verdict": {
            "thesis": verdict,
            "deploy_candidate": deploy,
            "note": verdict_note,
        },
        "symbol_errors": symbol_errors[:40],
        "signals_preview": [
            {
                "id": s.id,
                "symbol": s.symbol,
                "rank": s.rank,
                "direction": s.direction,
                "score": round(s.score, 2),
                "adx": round(s.adx, 2) if s.adx is not None else None,
                "signal_at": s.signal_at.isoformat(),
                "entry": s.entry,
            }
            for s in all_signals[:40]
        ],
    }
    # JSON-serialize datetimes in sample
    if payload["sample"]["first_signal"] is not None:
        payload["sample"]["first_signal"] = payload["sample"]["first_signal"].isoformat()
    if payload["sample"]["last_signal"] is not None:
        payload["sample"]["last_signal"] = payload["sample"]["last_signal"].isoformat()

    text = json.dumps(payload, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Wrote {args.out}", file=sys.stderr, flush=True)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
