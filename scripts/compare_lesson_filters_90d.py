#!/usr/bin/env python3
"""90-day DB backtest: old paper gates vs WUSDT-lesson skip rules.

Regenerates signals from ``market_candles`` (no exchange fetch) and applies
identical exit/retest logic. Variants differ only in ``lesson_skip_rules``.

  .venv/bin/python scripts/compare_lesson_filters_90d.py \\
      --days 90 --top 150 --workers 8 \\
      --out exports/lesson_filters_90d.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
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
from app.signals.lesson_filters import (
    COMBO_CORE,
    COMBO_FULL,
    LESSON_RULE_BB_SQUEEZE,
    LESSON_RULE_BULLISH_DIV,
    LESSON_RULE_NO_VOL_CONFIRM,
    LESSON_RULE_RSI_RISING,
    LESSON_RULE_VOL_LT_0_5,
    LESSON_RULE_WEAK_VOL,
)
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

METRIC_KEYS = (
    "trade_count",
    "win_rate",
    "net_profit",
    "profit_factor",
    "expectancy",
    "max_drawdown_percent",
    "total_fees",
)


@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    lesson_skip_rules: tuple[str, ...]


def _catalog() -> list[Variant]:
    return [
        Variant("old_rules", "Alte Strategie (Paper-Gates, keine Lesson-Skips)", ()),
        Variant(
            "new_skip_bullish_div",
            "NEU: Short skip bei bullischer Divergenz",
            (LESSON_RULE_BULLISH_DIV,),
        ),
        Variant(
            "new_skip_rsi_rising",
            "NEU: Short skip wenn RSI steigt",
            (LESSON_RULE_RSI_RISING,),
        ),
        Variant(
            "new_skip_no_vol_confirm",
            "NEU: Short skip Break ohne Volumenbestätigung",
            (LESSON_RULE_NO_VOL_CONFIRM,),
        ),
        Variant(
            "new_skip_bb_squeeze",
            "NEU: Skip bei Bollinger-Squeeze",
            (LESSON_RULE_BB_SQUEEZE,),
        ),
        Variant(
            "new_combo_core",
            "NEU Kombi: Divergenz + NoVolConfirm + RSI↑",
            COMBO_CORE,
        ),
        Variant(
            "new_combo_full",
            "NEU Kombi full: alle Lesson-Filter",
            COMBO_FULL,
        ),
    ]


def _pick(metrics: dict[str, float] | None) -> dict[str, float]:
    if not metrics:
        return {key: 0.0 for key in METRIC_KEYS}
    return {key: float(metrics.get(key, 0.0) or 0.0) for key in METRIC_KEYS}


def _base_config_kwargs(settings) -> dict[str, Any]:
    return {
        "timeframe": settings.primary_timeframe,
        "fee_percent": float(settings.paper_fee_percent),
        "slippage_percent": 0.05,
        "initial_capital": 5_000.0,
        "min_score": settings.signal_min_score,
        "min_risk_reward_ratio": settings.min_risk_reward_ratio,
        "atr_multiplier": settings.atr_multiplier,
        "max_atr_percent": settings.max_atr_percent,
        "expiry_multiplier": settings.signal_expiry_multiplier,
        "timeframes": tuple(settings.timeframes),
        # Single-TF for runtime; primary indicators still carry divergence/RSI/BB.
        "use_multi_timeframe": False,
        "cooldown_minutes": settings.signal_cooldown_minutes,
        "require_strong_signals": settings.signal_require_strong,
        "block_range_market": settings.signal_block_range_market,
        "min_adx": settings.signal_min_adx,
        "rsi_long_max": settings.signal_rsi_long_max,
        "rsi_short_min": settings.signal_rsi_short_min,
        "scale_out_enabled": True,
        "scale_out_fractions": tuple(settings.parsed_scale_out_fractions),
        "move_stop_to_breakeven_after_tp1": settings.paper_move_stop_to_breakeven,
        "tp_multipliers": tuple(settings.parsed_tp_multipliers),
        "retest_entry_enabled": settings.backtest_retest_entry_enabled,
        "retest_zone_near": settings.paper_retest_zone_near,
        "retest_zone_far": settings.paper_retest_zone_far,
        "retest_pending_multiplier": settings.paper_retest_pending_multiplier,
        "weights": DEFAULT_WEIGHTS.without_sentiment(),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if "error" not in r]
    failed = len(rows) - len(ok)
    with_trades = [r for r in ok if int(r["overall"]["trade_count"]) > 0]
    total_trades = sum(int(r["overall"]["trade_count"]) for r in ok)
    total_net = sum(float(r["overall"]["net_profit"]) for r in ok)
    total_fees = sum(float(r["overall"]["total_fees"]) for r in ok)
    lesson_skips = sum(int(r.get("signals_skipped_lesson", 0)) for r in ok)
    wr_num = sum(
        float(r["overall"]["win_rate"]) * int(r["overall"]["trade_count"]) for r in with_trades
    )
    pf_num = sum(
        float(r["overall"]["profit_factor"]) * int(r["overall"]["trade_count"])
        for r in with_trades
        if math.isfinite(float(r["overall"]["profit_factor"]))
        and float(r["overall"]["profit_factor"]) > 0
    )
    pf_den = sum(
        int(r["overall"]["trade_count"])
        for r in with_trades
        if math.isfinite(float(r["overall"]["profit_factor"]))
        and float(r["overall"]["profit_factor"]) > 0
    )
    dd_worst = max(
        (float(r["overall"]["max_drawdown_percent"]) for r in with_trades),
        default=0.0,
    )
    long_net = sum(float(r.get("long", {}).get("net_profit", 0.0)) for r in ok)
    short_net = sum(float(r.get("short", {}).get("net_profit", 0.0)) for r in ok)
    long_trades = sum(int(r.get("long", {}).get("trade_count", 0)) for r in ok)
    short_trades = sum(int(r.get("short", {}).get("trade_count", 0)) for r in ok)
    return {
        "symbols_ok": len(ok),
        "symbols_failed": failed,
        "symbols_with_trades": len(with_trades),
        "symbols_profitable": sum(1 for r in ok if float(r["overall"]["net_profit"]) > 0),
        "total_trades": total_trades,
        "total_net_profit": round(total_net, 2),
        "total_fees": round(total_fees, 2),
        "avg_win_rate": round(wr_num / total_trades, 4) if total_trades else 0.0,
        "avg_profit_factor": round(pf_num / pf_den, 4) if pf_den else 0.0,
        "worst_max_dd_pct": round(dd_worst, 2),
        "signals_skipped_lesson": lesson_skips,
        "long": {"trades": long_trades, "net_profit": round(long_net, 2)},
        "short": {"trades": short_trades, "net_profit": round(short_net, 2)},
    }


def _run_symbol_job(payload: dict[str, Any]) -> dict[str, Any]:
    logging.disable(logging.INFO)
    symbol = payload["symbol"]
    rank = payload["rank"]
    base_kwargs = dict(payload["base_kwargs"])
    weights_dump = base_kwargs.pop("weights")
    if isinstance(weights_dump, dict):
        base_kwargs["weights"] = StrategyWeights(**weights_dump)
    for key in ("timeframes", "scale_out_fractions", "tp_multipliers"):
        if isinstance(base_kwargs.get(key), list):
            base_kwargs[key] = tuple(base_kwargs[key])

    df = pd.DataFrame(payload["frame"])
    if "open_time" in df.columns:
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        df = df.set_index("open_time", drop=False)

    start = pd.Timestamp(payload["start"], tz="UTC")
    end = pd.Timestamp(payload["end"], tz="UTC")

    results: dict[str, dict[str, Any]] = {}
    for variant in payload["variants"]:
        key = variant["key"]
        rules = tuple(variant["lesson_skip_rules"])
        try:
            config = BacktestConfig(
                symbol=symbol,
                lesson_skip_rules=rules,
                **base_kwargs,
            )
            outcome = BacktestEngine(config).run(df)
            metrics = compute_metrics(outcome)
            overall = _pick(metrics.get("overall"))
            side = {
                "long": _pick(metrics.get("long")),
                "short": _pick(metrics.get("short")),
            }
            results[key] = {
                "symbol": symbol,
                "market_cap_rank": rank,
                "overall": overall,
                "long": side["long"],
                "short": side["short"],
                "signals_skipped_lesson": int(outcome.signals_skipped_lesson),
                "signals_generated": int(outcome.signals_generated),
                "bars": int(len(df)),
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
            }
        except Exception as exc:  # noqa: BLE001 — worker must not die
            results[key] = {
                "symbol": symbol,
                "market_cap_rank": rank,
                "error": str(exc),
            }
    return {"symbol": symbol, "rank": rank, "results": results}


async def _load_symbols(limit: int) -> list[tuple[str, int]]:
    async with session_scope() as session:
        stmt = (
            select(Asset.symbol, Asset.market_cap_rank)
            .where(
                Asset.is_active.is_(True),
                Asset.in_universe.is_(True),
                Asset.market_cap_rank.is_not(None),
            )
            .order_by(Asset.market_cap_rank.asc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
    return [(str(symbol).upper(), int(rank)) for symbol, rank in rows]


async def _load_all_frames(
    symbols: list[tuple[str, int]],
    timeframe: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = []
    async with session_scope() as session:
        repo = AssetRepository(session)
        warmup_start = start - timeframe_to_timedelta(timeframe) * WARMUP_CANDLES
        for idx, (symbol, rank) in enumerate(symbols, start=1):
            series = await repo.load_candle_series(
                symbol,
                timeframe,
                start_time=warmup_start,
                end_time=end,
                limit=100_000,
            )
            if series.is_empty:
                loaded.append({"symbol": symbol, "rank": rank, "error": "no candles"})
                continue
            df = series.to_dataframe().reset_index(drop=False)
            if "open_time" not in df.columns and df.index.name == "open_time":
                df = df.reset_index()
            # Require enough bars for warmup + ~half the window
            min_bars = WARMUP_CANDLES + max(24 * 30, 100)
            if len(df) < min_bars:
                loaded.append(
                    {
                        "symbol": symbol,
                        "rank": rank,
                        "error": f"insufficient bars: {len(df)} < {min_bars}",
                    }
                )
                continue
            loaded.append(
                {
                    "symbol": symbol,
                    "rank": rank,
                    "frame": df.to_dict(orient="list"),
                    "bars": len(df),
                }
            )
            if idx % 25 == 0 or idx == len(symbols):
                print(f"loaded candles {idx}/{len(symbols)}", file=sys.stderr, flush=True)
    return loaded


def _serialize_weights(weights: StrategyWeights) -> dict[str, float]:
    return weights.model_dump()


async def main() -> int:
    parser = argparse.ArgumentParser(description="90d old vs lesson-filter backtest")
    parser.add_argument("--top", type=int, default=150)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", type=Path, default=Path("exports/lesson_filters_90d.json"))
    parser.add_argument(
        "--variants",
        default="old_rules,new_combo_core,new_combo_full",
        help="Comma-separated variant keys (default: old + core + full)",
    )
    parser.add_argument("--all-variants", action="store_true", help="Run full catalog")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    logging.getLogger("app").setLevel(logging.ERROR)

    end = utc_now().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=args.days)

    catalog = {v.key: v for v in _catalog()}
    if args.all_variants:
        selected = list(catalog.values())
    else:
        keys = [k.strip() for k in args.variants.split(",") if k.strip()]
        missing = [k for k in keys if k not in catalog]
        if missing:
            print(f"Unknown variants: {missing}", file=sys.stderr)
            return 2
        selected = [catalog[k] for k in keys]

    print(
        f"Window {start.date()} → {end.date()} | top {args.top} | "
        f"variants={[v.key for v in selected]} | workers={args.workers}",
        file=sys.stderr,
        flush=True,
    )

    symbols = await _load_symbols(args.top)
    print(f"symbols requested: {len(symbols)}", file=sys.stderr, flush=True)

    t0 = time.time()
    frames = await _load_all_frames(
        symbols, settings.primary_timeframe, start, end
    )
    usable = [f for f in frames if "frame" in f]
    skipped_load = [f for f in frames if "error" in f]
    print(
        f"candles ready: {len(usable)} ok, {len(skipped_load)} skipped "
        f"({time.time() - t0:.1f}s)",
        file=sys.stderr,
        flush=True,
    )

    base_kwargs = _base_config_kwargs(settings)
    base_kwargs["weights"] = _serialize_weights(base_kwargs["weights"])
    for key in ("timeframes", "scale_out_fractions", "tp_multipliers"):
        if isinstance(base_kwargs.get(key), tuple):
            base_kwargs[key] = list(base_kwargs[key])

    variant_payload = [
        {"key": v.key, "label": v.label, "lesson_skip_rules": list(v.lesson_skip_rules)}
        for v in selected
    ]

    jobs = [
        {
            "symbol": item["symbol"],
            "rank": item["rank"],
            "frame": item["frame"],
            "base_kwargs": dict(base_kwargs),
            "variants": variant_payload,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        for item in usable
    ]

    per_variant: dict[str, list[dict[str, Any]]] = {v.key: [] for v in selected}
    # carry load errors into every variant
    for item in skipped_load:
        err = {
            "symbol": item["symbol"],
            "market_cap_rank": item["rank"],
            "error": item["error"],
        }
        for v in selected:
            per_variant[v.key].append(err)

    done = 0
    t1 = time.time()
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(_run_symbol_job, job) for job in jobs]
        for fut in as_completed(futures):
            result = fut.result()
            done += 1
            for key, row in result["results"].items():
                per_variant[key].append(row)
            if done % 10 == 0 or done == len(futures):
                print(
                    f"backtested {done}/{len(futures)} symbols "
                    f"({time.time() - t1:.0f}s)",
                    file=sys.stderr,
                    flush=True,
                )

    comparison = []
    old_agg = None
    for v in selected:
        agg = _aggregate(per_variant[v.key])
        entry = {
            "key": v.key,
            "label": v.label,
            "lesson_skip_rules": list(v.lesson_skip_rules),
            "aggregate": agg,
        }
        if v.key == "old_rules":
            old_agg = agg
        comparison.append(entry)

    if old_agg is not None:
        for entry in comparison:
            agg = entry["aggregate"]
            entry["delta_vs_old"] = {
                "total_net_profit": round(
                    agg["total_net_profit"] - old_agg["total_net_profit"], 2
                ),
                "total_trades": agg["total_trades"] - old_agg["total_trades"],
                "avg_win_rate": round(agg["avg_win_rate"] - old_agg["avg_win_rate"], 4),
                "avg_profit_factor": round(
                    agg["avg_profit_factor"] - old_agg["avg_profit_factor"], 4
                ),
            }

    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "type": "db_candle_backtest_lesson_filters",
            "description": (
                "Regenerate signals from DB 1h candles; identical exits/retest; "
                "variants differ only by lesson_skip_rules"
            ),
            "timeframe": settings.primary_timeframe,
            "use_multi_timeframe": False,
            "days": args.days,
            "top": args.top,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "initial_capital_per_symbol": 5000.0,
            "caveats": [
                "Per-symbol capital (not shared portfolio)",
                "Single-TF 1h (no 15m/4h/1d MTF in this run)",
                "Lesson filters applied post-signal on primary indicators",
                "Cannot recover trades never taken under looser gates in live paper",
            ],
        },
        "comparison": comparison,
        "headline": {},
    }

    old = next((c for c in comparison if c["key"] == "old_rules"), None)
    core = next((c for c in comparison if c["key"] == "new_combo_core"), None)
    full = next((c for c in comparison if c["key"] == "new_combo_full"), None)
    if old and (core or full):
        best_new = full or core
        payload["headline"] = {
            "old": old["aggregate"],
            "new": best_new["aggregate"],
            "delta": best_new.get("delta_vs_old"),
            "new_key": best_new["key"],
            "verdict": (
                "new_better"
                if best_new["aggregate"]["total_net_profit"]
                > old["aggregate"]["total_net_profit"]
                else "old_better"
            ),
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(json.dumps(payload["headline"], indent=2, default=str))
    print("\n=== 90d OLD vs NEW ===", file=sys.stderr)
    for entry in comparison:
        agg = entry["aggregate"]
        delta = entry.get("delta_vs_old") or {}
        print(
            f"{entry['key']:28} trades={agg['total_trades']:5} "
            f"net={agg['total_net_profit']:+10.2f} "
            f"Δ={delta.get('total_net_profit', 0):+10.2f} "
            f"WR={agg['avg_win_rate']:.1%} PF={agg['avg_profit_factor']:.2f} "
            f"lesson_skips={agg['signals_skipped_lesson']}",
            file=sys.stderr,
        )
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
