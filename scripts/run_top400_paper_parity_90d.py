"""Top400 × 90d Paper-Parity Backtest + kombinierte Equity.

Preload 1h candles, cache BTC 4h regime, ProcessPool workers.
Paper gates via BacktestConfig (Retest/TP/BE/Score/ADX/RSI/Regime).

    python scripts/run_top400_paper_parity_90d.py --top 400 --days 90 --workers 2 \\
        --out exports/top400_paper_parity_90d.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.backtesting.engine import WARMUP_CANDLES, BacktestConfig, BacktestEngine
from app.backtesting.metrics import compute_metrics
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.time import timeframe_to_timedelta, utc_now
from app.database.session import session_scope
from app.models.market import Asset
from app.repositories.asset_repository import AssetRepository
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

BTC_WEIGHT_PRESETS: dict[str, dict[str, float]] = {
    "old": {"1w": 0.35, "1d": 0.30, "4h": 0.25, "1h": 0.10, "12h": 0.20, "15m": 0.05},
    "new": {"1w": 0.10, "1d": 0.15, "4h": 0.35, "1h": 0.40, "12h": 0.20, "15m": 0.05},
}


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _paper_config_kwargs(settings, *, capital: float, fee: float, slip: float) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "timeframe": "1h",
        "fee_percent": fee,
        "slippage_percent": slip,
        "initial_capital": capital,
        "min_score": settings.signal_min_score,
        "min_risk_reward_ratio": settings.min_risk_reward_ratio,
        "atr_multiplier": settings.atr_multiplier,
        "max_atr_percent": settings.max_atr_percent,
        "expiry_multiplier": settings.signal_expiry_multiplier,
        "expiry_multiplier_after_tp1": settings.paper_expiry_multiplier_after_tp1,
        "timeframes": ("1h",),
        "use_multi_timeframe": False,
        "cooldown_minutes": settings.signal_cooldown_minutes,
        "require_strong_signals": settings.signal_require_strong,
        "block_range_market": settings.signal_block_range_market,
        "min_adx": settings.signal_min_adx,
        "rsi_long_max": settings.signal_rsi_long_max,
        "rsi_short_min": settings.signal_rsi_short_min,
        "regime_filter_enabled": settings.regime_filter_enabled,
        "scale_out_enabled": True,
        "scale_out_fractions": tuple(settings.parsed_scale_out_fractions),
        "move_stop_to_breakeven_after_tp1": settings.paper_move_stop_to_breakeven,
        "tp_multipliers": tuple(settings.parsed_tp_multipliers),
        "retest_entry_enabled": settings.backtest_retest_entry_enabled,
        "retest_zone_near": settings.paper_retest_zone_near,
        "retest_zone_far": settings.paper_retest_zone_far,
        "retest_pending_multiplier": settings.paper_retest_pending_multiplier,
        "retest_min_bars_in_zone": settings.paper_retest_min_bars_in_zone,
        # Optional: only present on builds with trendline gate.
        "retest_trendline_gate": bool(
            getattr(settings, "signal_trendline_gate_enabled", False)
        ),
        "retest_trendline_buffer_atr": float(
            getattr(settings, "signal_trendline_buffer_atr", 0.15)
        ),
        "retest_trendline_lookback": int(
            getattr(settings, "signal_trendline_lookback", 120)
        ),
        "retest_trendline_min_points": int(
            getattr(settings, "signal_trendline_min_points", 3)
        ),
        "retest_trendline_min_r2": float(
            getattr(settings, "signal_trendline_min_r2", 0.85)
        ),
        "retest_trendline_min_clearance_atr": float(
            getattr(settings, "signal_trendline_min_clearance_atr", 0.25)
        ),
        "short_max_score": settings.signal_short_max_score,
        "short_min_score": settings.signal_short_min_score,
        "weights": DEFAULT_WEIGHTS.without_sentiment(),
    }
    known = set(BacktestConfig.__dataclass_fields__)
    return {k: v for k, v in raw.items() if k in known}


def _serialize_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    out = dict(kwargs)
    w = out.get("weights")
    if isinstance(w, StrategyWeights):
        out["weights"] = w.model_dump()
    for key in ("tp_multipliers", "scale_out_fractions", "timeframes"):
        if key in out and isinstance(out[key], tuple):
            out[key] = list(out[key])
    return out


def _deserialize_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    out = dict(kwargs)
    if isinstance(out.get("weights"), dict):
        out["weights"] = StrategyWeights(**out["weights"])
    for key in ("tp_multipliers", "scale_out_fractions", "timeframes"):
        if key in out and isinstance(out[key], list):
            out[key] = tuple(out[key])
    return out


async def _load_symbols(limit: int) -> list[tuple[str, int]]:
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


async def _load_frame(
    symbol: str,
    timeframe: str,
    *,
    start: datetime,
    end: datetime,
) -> pd.DataFrame | None:
    warmup_start = start - timeframe_to_timedelta(timeframe) * WARMUP_CANDLES
    async with session_scope() as session:
        series = await AssetRepository(session).load_candle_series(
            symbol,
            timeframe,
            start_time=warmup_start,
            end_time=end,
            limit=100_000,
        )
    if series is None or series.is_empty:
        return None
    df = series.to_dataframe()
    if "open_time" in df.columns:
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        df = df.set_index("open_time", drop=False)
    return df


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    # CandleSeries.to_dataframe() uses DatetimeIndex named open_time — keep it.
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        raise ValueError("expected DatetimeIndex on candle frame")
    out = out.reset_index(drop=False)
    if "open_time" not in out.columns:
        # index may have been unnamed after some transforms
        out = out.rename(columns={out.columns[0]: "open_time"})
    # Deduplicate if both index level and column existed
    cols = list(out.columns)
    if cols.count("open_time") > 1:
        out = out.loc[:, ~out.columns.duplicated()]
    out["open_time"] = pd.to_datetime(out["open_time"], utc=True).astype(str)
    keep = [
        c
        for c in (
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "trade_count",
        )
        if c in out.columns
    ]
    return out[keep].to_dict(orient="records")


def _records_to_df(records: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if "open_time" not in df.columns:
        raise ValueError("candle records missing open_time")
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df = df.set_index("open_time", drop=True).sort_index()
    df.index = pd.DatetimeIndex(df.index, name="open_time")
    return df


def _trade_row(trade: Any, *, rank: int) -> dict[str, Any]:
    direction = trade.direction.value if hasattr(trade.direction, "value") else str(trade.direction)
    risk_amount = abs(float(trade.entry_price) - float(trade.stop_loss)) * float(trade.quantity)
    r_mult = (float(trade.net_pnl) / risk_amount) if risk_amount > 1e-12 else 0.0
    return {
        "symbol": trade.symbol,
        "rank": rank,
        "direction": direction,
        "entry_at": _iso(trade.entry_at),
        "exit_at": _iso(trade.exit_at),
        "entry_price": float(trade.entry_price),
        "exit_price": float(trade.exit_price) if trade.exit_price is not None else None,
        "stop_loss": float(trade.stop_loss),
        "net_pnl": float(trade.net_pnl),
        "fees": float(trade.fees),
        "score": float(trade.signal_score),
        "exit_reason": trade.exit_reason.value if trade.exit_reason else None,
        "holding_minutes": int(trade.holding_minutes),
        "risk_amount": risk_amount,
        "r_multiple": r_mult,
        "rr_planned": float(trade.risk_reward_planned),
    }


def _run_symbol_job(payload: dict[str, Any]) -> dict[str, Any]:
    logging.disable(logging.INFO)
    symbol = payload["symbol"]
    rank = payload["rank"]
    try:
        weights = payload.get("btc_tf_weights")
        if isinstance(weights, dict) and weights:
            from app.market_regime import bitcoin as btc_mod

            btc_mod.DEFAULT_TF_WEIGHTS.clear()
            btc_mod.DEFAULT_TF_WEIGHTS.update({str(k): float(v) for k, v in weights.items()})
        df = _records_to_df(payload["frame"])
        btc: dict[str, pd.DataFrame] = {}
        for tf, recs in (payload.get("btc_frames") or {}).items():
            btc[tf] = _records_to_df(recs)
        kwargs = _deserialize_kwargs(payload["base_kwargs"])
        config = BacktestConfig(symbol=symbol, **kwargs)
        outcome = BacktestEngine(config).run(df, btc_mtf_frames=btc or None)
        metrics = compute_metrics(outcome)
        overall = metrics.get("overall") or {}
        closed = [t for t in outcome.trades if t.is_closed and t.exit_at is not None]
        return {
            "symbol": symbol,
            "market_cap_rank": rank,
            "candles_loaded": len(df),
            "signals_generated": outcome.signals_generated,
            "trade_count": len(closed),
            "net_profit": float(overall.get("net_profit") or 0.0),
            "win_rate": float(overall.get("win_rate") or 0.0),
            "profit_factor": float(overall.get("profit_factor") or 0.0),
            "max_drawdown_percent": float(overall.get("max_drawdown_percent") or 0.0),
            "trades": [_trade_row(t, rank=rank) for t in closed],
        }
    except Exception as exc:  # noqa: BLE001
        return {"symbol": symbol, "market_cap_rank": rank, "error": str(exc)}


def _apply_paper_portfolio(
    trades: list[dict[str, Any]],
    *,
    start_equity: float,
    max_open: int,
    max_per_direction: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    def _parse(ts: str) -> datetime:
        return datetime.fromisoformat(ts)

    ordered = sorted(trades, key=lambda t: (_parse(t["entry_at"]), t["symbol"]))
    open_book: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for trade in ordered:
        entry = _parse(trade["entry_at"])
        open_book = [o for o in open_book if _parse(o["exit_at"]) > entry]
        dir_count = sum(1 for o in open_book if o["direction"] == trade["direction"])
        if len(open_book) >= max_open:
            skipped.append({**trade, "skip_reason": "max_open"})
            continue
        if dir_count >= max_per_direction:
            skipped.append({**trade, "skip_reason": "max_per_direction"})
            continue
        open_book.append(trade)
        accepted.append(trade)

    equity = float(start_equity)
    peak = equity
    start_t = ordered[0]["entry_at"] if ordered else _iso(utc_now())
    curve: list[dict[str, Any]] = [{"t": start_t, "equity": equity, "pnl": 0.0}]
    for trade in sorted(accepted, key=lambda t: _parse(t["exit_at"])):
        equity += float(trade["net_pnl"])
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        curve.append(
            {
                "t": trade["exit_at"],
                "equity": round(equity, 4),
                "pnl": round(float(trade["net_pnl"]), 4),
                "symbol": trade["symbol"],
                "dd_pct": round(dd, 4),
            }
        )
    return accepted, skipped, curve


def _summarize(
    trades: list[dict[str, Any]],
    *,
    start_equity: float,
    curve: list[dict[str, Any]],
) -> dict[str, Any]:
    n = len(trades)
    wins = [t for t in trades if float(t["net_pnl"]) > 0]
    losses = [t for t in trades if float(t["net_pnl"]) < 0]
    net = sum(float(t["net_pnl"]) for t in trades)
    fees = sum(float(t["fees"]) for t in trades)
    gp = sum(float(t["net_pnl"]) for t in wins)
    gl = abs(sum(float(t["net_pnl"]) for t in losses))
    pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)
    rs = [float(t["r_multiple"]) for t in trades if float(t["risk_amount"]) > 0]
    end_eq = float(curve[-1]["equity"]) if curve else start_equity
    max_dd = max((float(p.get("dd_pct", 0.0)) for p in curve), default=0.0)
    by_side: dict[str, dict[str, float]] = {}
    for side in ("LONG", "SHORT", "STRONG_LONG", "STRONG_SHORT"):
        subset = [t for t in trades if t["direction"] == side]
        if not subset:
            continue
        s_net = sum(float(t["net_pnl"]) for t in subset)
        s_wins = sum(1 for t in subset if float(t["net_pnl"]) > 0)
        by_side[side] = {
            "trades": float(len(subset)),
            "net_pnl": round(s_net, 2),
            "win_rate": round(s_wins / len(subset) * 100.0, 2),
        }
    exits: dict[str, int] = defaultdict(int)
    for t in trades:
        exits[str(t.get("exit_reason") or "unknown")] += 1
    daily: list[dict[str, Any]] = []
    if curve:
        by_day: dict[str, dict[str, Any]] = {}
        for p in curve:
            by_day[str(p["t"])[:10]] = p
        for day in sorted(by_day):
            p = by_day[day]
            daily.append(
                {
                    "date": day,
                    "equity": float(p["equity"]),
                    "pnl_cum": round(float(p["equity"]) - start_equity, 2),
                }
            )
    return {
        "closed": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n * 100.0, 2) if n else 0.0,
        "net_pnl": round(net, 2),
        "fees": round(fees, 2),
        "profit_factor": round(pf, 3) if math.isfinite(pf) else 99.0,
        "expectancy_usd": round(net / n, 2) if n else 0.0,
        "total_r": round(sum(rs), 2),
        "expectancy_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
        "start_equity": start_equity,
        "end_equity": round(end_eq, 2),
        "return_pct": round((end_eq / start_equity - 1.0) * 100.0, 2) if start_equity else 0.0,
        "max_drawdown_pct": round(max_dd, 2),
        "by_side": by_side,
        "exits": dict(exits),
        "equity_daily": daily,
    }


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=400)
    parser.add_argument("--days", type=float, default=90)
    parser.add_argument(
        "--since",
        type=str,
        default="",
        help="ISO start time (UTC); overrides --days when set",
    )
    parser.add_argument(
        "--btc-weights",
        type=str,
        default="current",
        choices=("current", "old", "new"),
        help="BTC MTF weight preset for regime blend/veto",
    )
    parser.add_argument("--capital", type=float, default=0.0)
    parser.add_argument("--fee", type=float, default=-1.0)
    parser.add_argument("--slippage", type=float, default=0.0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--out", type=str, default="exports/top400_paper_parity_90d.json")
    args = parser.parse_args()

    configure_logging()
    logging.getLogger("app").setLevel(logging.WARNING)
    settings = get_settings()
    capital = float(args.capital or settings.paper_initial_balance or 5000.0)
    fee = float(settings.paper_fee_percent if args.fee < 0 else args.fee)
    end = utc_now()
    if str(args.since).strip():
        start = datetime.fromisoformat(str(args.since).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        else:
            start = start.astimezone(timezone.utc)
    else:
        start = end - timedelta(days=float(args.days))
    btc_tf_weights = BTC_WEIGHT_PRESETS.get(str(args.btc_weights))
    base_kwargs = _serialize_kwargs(
        _paper_config_kwargs(settings, capital=capital, fee=fee, slip=float(args.slippage))
    )

    symbols = await _load_symbols(args.top)
    t0 = time.time()
    print(
        f"Top{len(symbols)} paper-parity · btc_weights={args.btc_weights} · "
        f"workers={args.workers} · fee={fee}% slip={args.slippage}% capital={capital} · "
        f"{start.isoformat()}→{end.isoformat()}",
        file=sys.stderr,
        flush=True,
    )
    if btc_tf_weights:
        print(f"  BTC TF weights: {btc_tf_weights}", file=sys.stderr, flush=True)

    btc_tfs = tuple(
        tf.strip()
        for tf in str(
            getattr(settings, "market_regime_btc_timeframes", "1h,4h,1d,1w")
        ).split(",")
        if tf.strip()
    ) or ("1h", "4h", "1d", "1w")
    print(f"Loading BTC regime TFs {btc_tfs}...", file=sys.stderr, flush=True)
    btc_frames_ser: dict[str, list[dict[str, Any]]] = {}
    for tf in btc_tfs:
        btc_df = await _load_frame(
            settings.regime_btc_symbol.upper(), tf, start=start, end=end
        )
        if btc_df is not None:
            btc_frames_ser[tf] = _df_to_records(btc_df)
        print(
            f"  BTC {tf} bars={0 if btc_df is None else len(btc_df)}",
            file=sys.stderr,
            flush=True,
        )

    print("Preloading 1h frames...", file=sys.stderr, flush=True)
    jobs: list[dict[str, Any]] = []
    load_failed = 0
    for idx, (symbol, rank) in enumerate(symbols, start=1):
        df = await _load_frame(symbol, "1h", start=start, end=end)
        if df is None or len(df) < WARMUP_CANDLES + 10:
            load_failed += 1
            continue
        jobs.append(
            {
                "symbol": symbol,
                "rank": rank,
                "frame": _df_to_records(df),
                "btc_frames": btc_frames_ser,
                "base_kwargs": base_kwargs,
                "btc_tf_weights": btc_tf_weights,
            }
        )
        if idx % 50 == 0 or idx == len(symbols):
            print(f"  loaded {idx}/{len(symbols)} (jobs={len(jobs)})", file=sys.stderr, flush=True)

    print(f"Simulating {len(jobs)} symbols...", file=sys.stderr, flush=True)
    per_symbol: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    done = 0
    workers = max(1, int(args.workers))

    def _consume(row: dict[str, Any]) -> None:
        nonlocal done
        done += 1
        if "error" in row:
            per_symbol.append(row)
        else:
            trades = row.pop("trades", [])
            per_symbol.append(row)
            all_trades.extend(trades)
        if done % 10 == 0 or done == len(jobs):
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed else 0
            eta_m = (len(jobs) - done) / rate / 60 if rate else 0
            print(
                f"[{done}/{len(jobs)}] last={row.get('symbol')} "
                f"trades={row.get('trade_count', 0)} · {elapsed/60:.1f}m · ETA {eta_m:.0f}m",
                file=sys.stderr,
                flush=True,
            )
            # Checkpoint for partial canvas
            accepted, skipped, curve = _apply_paper_portfolio(
                all_trades,
                start_equity=capital,
                max_open=int(settings.paper_max_open_positions),
                max_per_direction=int(settings.paper_max_open_per_direction),
            )
            kpi = _summarize(accepted, start_equity=capital, curve=curve)
            _write_checkpoint(
                Path(args.out).with_name("top400_paper_parity_90d.partial.json"),
                {
                    "partial": True,
                    "done": done,
                    "total": len(jobs),
                    "window": {
                        "days": round((end - start).total_seconds() / 86400.0, 2),
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                    },
                    "config": {
                        "top_n": len(symbols),
                        "jobs": len(jobs),
                        "workers": workers,
                        "capital": capital,
                        "fee_percent": fee,
                        "slippage_percent": float(args.slippage),
                        "btc_weights": args.btc_weights,
                        "btc_tf_weights": btc_tf_weights,
                        "paper_max_open_positions": settings.paper_max_open_positions,
                        "paper_max_open_per_direction": settings.paper_max_open_per_direction,
                    },
                    "kpi_paper_book": kpi,
                    "independent": {
                        "raw_trades": len(all_trades),
                        "accepted_trades": len(accepted),
                        "skipped_by_caps": len(skipped),
                        "raw_net_pnl": round(sum(float(t["net_pnl"]) for t in all_trades), 2),
                    },
                    "equity_daily": kpi.get("equity_daily"),
                },
            )

    if workers <= 1:
        for job in jobs:
            _consume(_run_symbol_job(job))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_run_symbol_job, job): job["symbol"] for job in jobs}
            for fut in as_completed(futs):
                try:
                    _consume(fut.result())
                except Exception as exc:  # noqa: BLE001
                    _consume({"symbol": futs[fut], "market_cap_rank": 0, "error": str(exc)})

    accepted, skipped, curve = _apply_paper_portfolio(
        all_trades,
        start_equity=capital,
        max_open=int(settings.paper_max_open_positions),
        max_per_direction=int(settings.paper_max_open_per_direction),
    )
    kpi = _summarize(accepted, start_equity=capital, curve=curve)
    uncapped_net = sum(float(t["net_pnl"]) for t in all_trades)
    with_trades = [r for r in per_symbol if "error" not in r and int(r.get("trade_count", 0)) > 0]
    top_winners = sorted(with_trades, key=lambda r: float(r["net_profit"]), reverse=True)[:15]
    top_losers = sorted(with_trades, key=lambda r: float(r["net_profit"]))[:15]
    failed = sum(1 for r in per_symbol if "error" in r) + load_failed

    payload = {
        "generated_at": utc_now().isoformat(),
        "label": f"top400_paper_parity_{args.btc_weights}",
        "runtime_seconds": round(time.time() - t0, 1),
        "window": {
            "days": round((end - start).total_seconds() / 86400.0, 2),
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "config": {
            "top_n": len(symbols),
            "jobs": len(jobs),
            "workers": workers,
            "capital": capital,
            "fee_percent": fee,
            "slippage_percent": float(args.slippage),
            "timeframe": "1h",
            "btc_weights": args.btc_weights,
            "btc_tf_weights": btc_tf_weights,
            "btc_regime_tfs": list(btc_frames_ser.keys()),
            "retest_entry_enabled": settings.backtest_retest_entry_enabled,
            "signal_min_score": settings.signal_min_score,
            "signal_short_min_score": settings.signal_short_min_score,
            "signal_short_max_score": settings.signal_short_max_score,
            "signal_require_strong": settings.signal_require_strong,
            "regime_filter_enabled": settings.regime_filter_enabled,
            "tp_multipliers": settings.tp_multipliers,
            "scale_out_fractions": settings.paper_scale_out_fractions,
            "paper_max_open_positions": settings.paper_max_open_positions,
            "paper_max_open_per_direction": settings.paper_max_open_per_direction,
            "candle_source": "db",
            "note": "1h primary + BTC MTF regime; paper caps on combined book equity.",
        },
        "kpi_paper_book": kpi,
        "independent": {
            "symbols_ok": len(jobs) - sum(1 for r in per_symbol if "error" in r),
            "symbols_failed": failed,
            "raw_trades": len(all_trades),
            "raw_net_pnl": round(uncapped_net, 2),
            "accepted_trades": len(accepted),
            "skipped_by_caps": len(skipped),
        },
        "top_winners": [
            {"symbol": r["symbol"], "rank": r["market_cap_rank"], "trades": r["trade_count"], "net": round(float(r["net_profit"]), 2)}
            for r in top_winners
        ],
        "top_losers": [
            {"symbol": r["symbol"], "rank": r["market_cap_rank"], "trades": r["trade_count"], "net": round(float(r["net_profit"]), 2)}
            for r in top_losers
        ],
        "equity_curve": curve,
        "skip_reasons": {
            k: sum(1 for s in skipped if s.get("skip_reason") == k)
            for k in ("max_open", "max_per_direction")
        },
        "results": per_symbol,
        "trades_sample": accepted[:50],
    }
    out = Path(args.out)
    _write_checkpoint(out, payload)
    print(json.dumps({"wrote": str(out), "kpi": kpi, "independent": payload["independent"]}, indent=2))
    return 0


if __name__ == "__main__":
    # Windows / fork safety
    try:
        import multiprocessing as mp

        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass
    raise SystemExit(asyncio.run(main()))
