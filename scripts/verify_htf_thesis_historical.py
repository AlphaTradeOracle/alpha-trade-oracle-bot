"""Historical verification: IST fill vs 4h-breakout thesis.

Fetches months of 1h/4h/1d candles from the exchange, regenerates STRONG
signals with the live BacktestEngine gates (MTF), then branches:

  Arm IST  — fill at next 1h open (engine parity)
  Arm Thesis — wait for 4h close beyond lookback high/low

Exits: paper-style $100×10, fee 0.1%, TP 2/4/6R scale-out, BE after TP1.
Does NOT change live strategy. JSON → stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.backtesting.engine import (
    WARMUP_CANDLES,
    BacktestConfig,
    BacktestEngine,
    BacktestOutcome,
    _index_time,
)
from app.container import build_container
from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, timeframe_to_timedelta, utc_now
from app.database.session import session_scope
from app.market_data.types import Candle, CandleSeries
from app.models.market import Asset
from app.signals.types import SignalResult

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simulate_htf_breakout_thesis as htf  # noqa: E402

MARGIN = Decimal("100")
LEVERAGE = Decimal("10")
FEE = Decimal("0.001")


@dataclass
class SignalEvent:
    symbol: str
    direction: str
    score: float
    signal_index: int
    signal_time: datetime
    entry_ref: float
    stop: float
    tp1: float
    tp2: float
    tp3: float


def _series_to_candles(series: CandleSeries) -> list[Candle]:
    return list(series.candles)


def _df_to_candles(df: pd.DataFrame) -> list[Candle]:
    out: list[Candle] = []
    for ts, row in df.iterrows():
        open_time = ensure_utc(ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts)
        out.append(
            Candle(
                open_time=open_time,
                close_time=open_time + timeframe_to_timedelta("1h"),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                is_closed=True,
            )
        )
    return out


def _agg(rows: list[htf.ReplayResult]) -> dict[str, Any]:
    a = htf._agg(rows)
    ec: dict[str, int] = {}
    for r in rows:
        ec[r.exit_reason] = ec.get(r.exit_reason, 0) + 1
    a["exit_counts"] = ec
    return a


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
    return [(str(s).upper(), int(r)) for s, r in rows]


async def _fetch_tf(provider, symbol: str, tf: str, start: datetime, end: datetime) -> CandleSeries | None:
    try:
        series = await provider.get_candles(
            symbol, tf, limit=100_000, start_time=start, end_time=end
        )
        if series.is_empty or len(series) < WARMUP_CANDLES + 10:
            return None
        return series
    except Exception as exc:  # noqa: BLE001
        print(f"  fetch fail {symbol} {tf}: {exc}", file=sys.stderr)
        return None


def _collect_signals(
    engine: BacktestEngine,
    mtf_frames: dict[str, pd.DataFrame],
    *,
    eval_start: datetime,
) -> list[SignalEvent]:
    primary_tf = engine._config.timeframe
    primary_df = mtf_frames[primary_tf]
    outcome = BacktestOutcome(config=engine._config)
    last_entry_at: datetime | None = None
    events: list[SignalEvent] = []
    total = len(primary_df)

    for i in range(WARMUP_CANDLES, total - 2):
        cutoff = ensure_utc(_index_time(primary_df, i))
        if cutoff < eval_start:
            continue
        signal = engine._generate_signal_mtf(mtf_frames, cutoff, i)
        if not engine._should_take_signal(signal, primary_df, i, last_entry_at, outcome):
            continue
        assert signal is not None and signal.risk is not None
        risk = signal.risk
        entry_ref = float((risk.entry_low + risk.entry_high) / 2)
        events.append(
            SignalEvent(
                symbol=engine._config.symbol,
                direction=signal.direction.value,
                score=float(signal.score),
                signal_index=i,
                signal_time=cutoff,
                entry_ref=entry_ref,
                stop=float(risk.stop_loss),
                tp1=float(risk.take_profit_1),
                tp2=float(risk.take_profit_2),
                tp3=float(risk.take_profit_3),
            )
        )
        # Cooldown anchor = signal bar time (approx live)
        last_entry_at = cutoff
        outcome.signals_generated += 1

    return events


def _replay_ist(
    event: SignalEvent,
    primary_df: pd.DataFrame,
    candles_1h: list[Candle],
) -> htf.ReplayResult:
    entry_index = event.signal_index + 1
    if entry_index >= len(primary_df):
        return htf.ReplayResult(
            arm="ist",
            pnl=0.0,
            fees=0.0,
            exit_reason="skipped_no_fill_bar",
            entry=event.entry_ref,
            stop_loss=event.stop,
            tp1=event.tp1,
            tp2=event.tp2,
            tp3=event.tp3,
            filled=False,
            note="no_next_bar",
        )
    fill_time = ensure_utc(_index_time(primary_df, entry_index))
    fill_price = Decimal(str(float(primary_df["open"].iloc[entry_index])))
    stop = Decimal(str(event.stop))
    is_long = SignalDirection(event.direction).is_long
    # Keep signal TPs but if stop side wrong vs fill, rebuild
    if (is_long and stop >= fill_price) or ((not is_long) and stop <= fill_price):
        return htf.ReplayResult(
            arm="ist",
            pnl=0.0,
            fees=0.0,
            exit_reason="skipped_invalid_sl",
            entry=float(fill_price),
            stop_loss=float(stop),
            tp1=event.tp1,
            tp2=event.tp2,
            tp3=event.tp3,
            filled=False,
            note="stop_wrong_side",
        )
    tp1, tp2, tp3 = (
        Decimal(str(event.tp1)),
        Decimal(str(event.tp2)),
        Decimal(str(event.tp3)),
    )
    # Re-anchor TPs as 2/4/6R from fill for fair sizing vs thesis
    tp1, tp2, tp3 = htf._levels_from_entry_sl(fill_price, stop, is_long)
    expiry = fill_time + 4 * htf._tf_delta("1h")
    return htf._replay_from_fill(
        arm="ist",
        direction=event.direction,
        entry=fill_price,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        fill_time=fill_time,
        candles=candles_1h,
        expiry_at=expiry,
    )


def _replay_thesis(
    event: SignalEvent,
    candles_1h: list[Candle],
    candles_4h: list[Candle],
) -> tuple[htf.ArmResult, htf.ReplayResult]:
    trade = htf.TradeInput(
        id=event.signal_index,
        symbol=event.symbol,
        direction=event.direction,
        status="signal",
        timeframe="1h",
        entry=event.entry_ref,
        stop_loss=event.stop,
        tp1=event.tp1,
        tp2=event.tp2,
        tp3=event.tp3,
        qty=float(MARGIN * LEVERAGE / Decimal(str(event.entry_ref))),
        notional=float(MARGIN * LEVERAGE),
        opened_at=event.signal_time,
        expires_at=event.signal_time + timedelta(days=htf.PENDING_DAYS),
        closed_at=None,
        actual_pnl=0.0,
        actual_fees=0.0,
        actual_exit=None,
        signal_created_at=event.signal_time,
    )
    arm = htf._arm_htf_breakout(trade, candles_4h)
    if arm.status != "filled" or arm.fill_price is None or arm.fill_time is None or arm.stop is None:
        skip = htf.ReplayResult(
            arm="thesis",
            pnl=0.0,
            fees=0.0,
            exit_reason=arm.status,
            entry=event.entry_ref,
            stop_loss=event.stop,
            tp1=event.tp1,
            tp2=event.tp2,
            tp3=event.tp3,
            filled=False,
            note=arm.note or arm.status,
        )
        return arm, skip

    fill = Decimal(str(arm.fill_price))
    stop = Decimal(str(arm.stop))
    is_long = SignalDirection(event.direction).is_long
    if (is_long and stop >= fill) or ((not is_long) and stop <= fill):
        skip = htf.ReplayResult(
            arm="thesis",
            pnl=0.0,
            fees=0.0,
            exit_reason="skipped_invalid_sl",
            entry=float(fill),
            stop_loss=float(stop),
            tp1=event.tp1,
            tp2=event.tp2,
            tp3=event.tp3,
            filled=False,
            note="invalid_stop",
        )
        return arm, skip

    tp1, tp2, tp3 = htf._levels_from_entry_sl(fill, stop, is_long)
    expiry = ensure_utc(arm.fill_time) + 4 * htf._tf_delta("4h")
    result = htf._replay_from_fill(
        arm="thesis",
        direction=event.direction,
        entry=fill,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        fill_time=ensure_utc(arm.fill_time),
        candles=candles_1h,
        expiry_at=expiry,
    )
    return arm, result


async def _run_symbol(
    container,
    settings,
    symbol: str,
    rank: int,
    *,
    days: int,
    end: datetime,
) -> dict[str, Any]:
    # Need warmup + 4h lookback buffer before eval window
    eval_start = end - timedelta(days=days)
    fetch_start = eval_start - timedelta(days=40)  # warmup + lookback buffer

    tfs = ("1h", "4h", "1d")  # skip 15m for speed; still MTF
    frames: dict[str, pd.DataFrame] = {}
    series_map: dict[str, CandleSeries] = {}
    for tf in tfs:
        series = await _fetch_tf(container.provider, symbol, tf, fetch_start, end)
        if series is None:
            if tf == "1h":
                return {"symbol": symbol, "rank": rank, "error": "no_1h"}
            continue
        frames[tf] = series.to_dataframe()
        series_map[tf] = series

    if "1h" not in frames or "4h" not in frames:
        return {"symbol": symbol, "rank": rank, "error": "missing_tf"}

    config = BacktestConfig.from_settings(
        settings,
        symbol=symbol,
        timeframe="1h",
        use_multi_timeframe=True,
        timeframes=tfs,
        fee_percent=0.1,
        slippage_percent=0.05,
        initial_capital=5_000.0,
    )
    engine = BacktestEngine(config)
    events = _collect_signals(engine, frames, eval_start=eval_start)
    candles_1h = _series_to_candles(series_map["1h"])
    candles_4h = _series_to_candles(series_map["4h"])
    primary_df = frames["1h"]

    ist_rows: list[htf.ReplayResult] = []
    thesis_rows: list[htf.ReplayResult] = []
    details: list[dict[str, Any]] = []

    for ev in events:
        ist = _replay_ist(ev, primary_df, candles_1h)
        arm, thesis = _replay_thesis(ev, candles_1h, candles_4h)
        ist_rows.append(ist)
        thesis_rows.append(thesis)
        details.append(
            {
                "symbol": symbol,
                "direction": ev.direction,
                "score": round(ev.score, 2),
                "signal_time": ev.signal_time.isoformat(),
                "ist": asdict(ist),
                "thesis": asdict(thesis),
                "thesis_arm": asdict(arm),
                "delta": round(thesis.pnl - ist.pnl, 4),
            }
        )

    return {
        "symbol": symbol,
        "rank": rank,
        "signals": len(events),
        "candles_1h": len(candles_1h),
        "candles_4h": len(candles_4h),
        "ist": _agg(ist_rows) if ist_rows else {"n": 0, "total_pnl": 0.0},
        "thesis": _agg(thesis_rows) if thesis_rows else {"n": 0, "total_pnl": 0.0},
        "details": details,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()

    logging.disable(logging.INFO)
    settings = get_settings()
    configure_logging("ERROR", json_output=False)
    container = build_container(settings)

    end = utc_now()
    symbols = await _load_top_symbols(args.top)
    if not symbols:
        print("No universe symbols", file=sys.stderr)
        await container.aclose()
        return 1

    print(
        f"Historical thesis verify · top={len(symbols)} · days={args.days} · "
        f"eval { (end - timedelta(days=args.days)).date() } → {end.date()}",
        file=sys.stderr,
        flush=True,
    )

    per_symbol: list[dict[str, Any]] = []
    all_ist: list[htf.ReplayResult] = []
    all_thesis: list[htf.ReplayResult] = []
    delta_samples: list[dict[str, Any]] = []
    summary_path = Path("/tmp/htf_hist_summary.json")

    try:
        for idx, (symbol, rank) in enumerate(symbols, start=1):
            print(f"[{idx}/{len(symbols)}] #{rank} {symbol} ...", file=sys.stderr, flush=True)
            result = await _run_symbol(
                container, settings, symbol, rank, days=args.days, end=end
            )
            slim = {k: v for k, v in result.items() if k != "details"}
            per_symbol.append(slim)
            if result.get("error"):
                print(f"  skip: {result['error']}", file=sys.stderr, flush=True)
                continue
            print(
                f"  signals={result['signals']} ist_pnl={result['ist'].get('total_pnl')} "
                f"thesis_pnl={result['thesis'].get('total_pnl')}",
                file=sys.stderr,
                flush=True,
            )
            for d in result.get("details") or []:
                all_ist.append(htf.ReplayResult(**d["ist"]))
                all_thesis.append(htf.ReplayResult(**d["thesis"]))
                delta_samples.append(
                    {
                        "symbol": d["symbol"],
                        "direction": d["direction"],
                        "score": d["score"],
                        "signal_time": d["signal_time"],
                        "ist_pnl": d["ist"]["pnl"],
                        "thesis_pnl": d["thesis"]["pnl"],
                        "thesis_filled": d["thesis"]["filled"],
                        "thesis_exit": d["thesis"]["exit_reason"],
                        "delta": d["delta"],
                    }
                )
            # checkpoint summary after each symbol
            try:
                summary_path.write_text(
                    json.dumps(
                        {
                            "progress": f"{idx}/{len(symbols)}",
                            "ist_pnl_so_far": round(sum(r.pnl for r in all_ist), 2),
                            "thesis_pnl_so_far": round(sum(r.pnl for r in all_thesis), 2),
                            "signals_so_far": len(all_ist),
                        }
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
            # free per-symbol trade details
            result["details"] = []
    finally:
        await container.aclose()

    ist_agg = _agg(all_ist) if all_ist else {"n": 0, "total_pnl": 0.0}
    thesis_agg = _agg(all_thesis) if all_thesis else {"n": 0, "total_pnl": 0.0}
    thesis_filled = _agg([r for r in all_thesis if r.filled]) if all_thesis else {"n": 0, "total_pnl": 0.0}

    helps = hurts = same = 0
    for d in delta_samples:
        delta = d.get("delta")
        if delta is None:
            continue
        if delta > 0.01:
            helps += 1
        elif delta < -0.01:
            hurts += 1
        else:
            same += 1

    skip_counts: dict[str, int] = {}
    for r in all_thesis:
        if not r.filled:
            skip_counts[r.exit_reason] = skip_counts.get(r.exit_reason, 0) + 1

    ist_pnl = float(ist_agg.get("total_pnl", 0))
    thesis_pnl = float(thesis_agg.get("total_pnl", 0))
    filled_pnl = float(thesis_filled.get("total_pnl", 0))
    filled_n = int(thesis_filled.get("n", 0))
    if filled_n >= 30 and filled_pnl > 0 and thesis_pnl > ist_pnl:
        verdict = "SUPPORTS_THESIS"
        verdict_text = "These schlägt IST und Fills sind netto positiv — vorsichtige Unterstützung."
    elif thesis_pnl > ist_pnl + 50 and filled_pnl >= min(0.0, ist_pnl) * 0.5:
        verdict = "WEAK_SUPPORT_FILTER_ONLY"
        verdict_text = "These weniger Verlust / mehr PnL als IST, aber Fills nicht klar als Edge belegt."
    elif thesis_pnl > ist_pnl:
        verdict = "WEAK_SUPPORT_FILTER_ONLY"
        verdict_text = "These besser im Aggregate, aber nicht live-reif ohne positive Fill-Qualität."
    else:
        verdict = "REFUTES_LIVE_ROLLOUT"
        verdict_text = "These schlägt IST nicht klar — Live-Rollout nicht empfohlen."

    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "type": "historical_signal_regeneration",
            "top": args.top,
            "days": args.days,
            "timeframes": ["1h", "4h", "1d"],
            "gates": "BacktestEngine live defaults (STRONG, score, ADX, RANGE, RSI)",
            "ist": "next 1h open after signal bar",
            "thesis": f"4h close beyond {htf.LOOKBACK_4H}-bar lookback, pending {htf.PENDING_DAYS}d",
            "sizing": "$100 x10 fee 0.1% TP 2/4/6R",
            "candle_source": "exchange_api",
            "live_changed": False,
        },
        "sample": {
            "symbols_requested": len(symbols),
            "symbols_ok": sum(1 for s in per_symbol if not s.get("error")),
            "signals": len(all_ist),
        },
        "ist": ist_agg,
        "thesis": thesis_agg,
        "thesis_filled_only": thesis_filled,
        "delta_total_pnl": round(thesis_pnl - ist_pnl, 2),
        "help_hurt": {"helps": helps, "hurts": hurts, "same": same},
        "skip_counts": skip_counts,
        "verdict": verdict,
        "verdict_text": verdict_text,
        "per_symbol": per_symbol,
        "top_deltas": sorted(delta_samples, key=lambda x: abs(x.get("delta") or 0), reverse=True)[:40],
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
