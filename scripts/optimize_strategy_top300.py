#!/usr/bin/env python3
"""Top-300 / 30d Strategy-Optimierung gegen DB-Candles (parallel).

1) Laedt Primary-TF Kerzen einmal pro Symbol (DB)
2) Spielt Varianten parallel je Symbol (ProcessPool)
3) Rankt nach Summe Net-Profit

  python scripts/optimize_strategy_top300.py --top 300 --days 30 --workers 8 \\
    --out exports/optimize_top300_30d.json
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
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.backtesting.engine import WARMUP_CANDLES, BacktestConfig, BacktestEngine
from app.backtesting.metrics import compute_metrics
from app.container import build_container
from app.core.config import get_settings
from app.core.enums import ScoreCategory
from app.core.logging import configure_logging
from app.core.time import timeframe_to_timedelta, utc_now
from app.database.session import session_scope
from app.models.market import Asset
from app.repositories.asset_repository import AssetRepository
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


def _pick(metrics: dict[str, float] | None) -> dict[str, float]:
    if not metrics:
        return {key: 0.0 for key in METRIC_KEYS}
    return {key: float(metrics.get(key, 0.0) or 0.0) for key in METRIC_KEYS}


def _shift_weight(
    base: StrategyWeights,
    *,
    boost: ScoreCategory,
    delta: float,
) -> StrategyWeights:
    d = {cat: val for cat, val in base.as_dict().items() if cat != ScoreCategory.SENTIMENT}
    d[boost] = d.get(boost, 0.0) + delta
    if d[boost] < 0 or d[boost] > 1.0:
        raise ValueError(f"Invalid boost for {boost.value}: {d[boost]}")
    others_total = sum(v for k, v in d.items() if k != boost)
    if others_total <= 0:
        raise ValueError("No weight left to redistribute")
    factor = (others_total - delta) / others_total
    for key in list(d):
        if key != boost:
            d[key] *= factor
    return StrategyWeights(
        trend=d[ScoreCategory.TREND],
        momentum=d[ScoreCategory.MOMENTUM],
        volume=d[ScoreCategory.VOLUME],
        market_structure=d[ScoreCategory.MARKET_STRUCTURE],
        multi_timeframe=d[ScoreCategory.MULTI_TIMEFRAME],
        volatility=d[ScoreCategory.VOLATILITY],
        sentiment=0.0,
        risk_reward=d[ScoreCategory.RISK_REWARD],
    )


@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    group: str
    overrides: dict[str, Any]


def _catalog() -> list[Variant]:
    baseline_w = DEFAULT_WEIGHTS.without_sentiment()
    return [
        Variant("baseline", "Current live defaults", "baseline", {}),
        Variant("score_70", "Min score 70", "gates", {"min_score": 70.0}),
        Variant("score_78", "Min score 78", "gates", {"min_score": 78.0}),
        Variant("score_82", "Min score 82", "gates", {"min_score": 82.0}),
        Variant("no_strong", "Allow non-STRONG", "gates", {"require_strong_signals": False}),
        Variant("allow_range", "Allow RANGE phase", "gates", {"block_range_market": False}),
        Variant("adx_15", "Min ADX 15", "filters", {"min_adx": 15.0}),
        Variant("adx_25", "Min ADX 25", "filters", {"min_adx": 25.0}),
        Variant("adx_30", "Min ADX 30", "filters", {"min_adx": 30.0}),
        Variant("rsi_long_70", "RSI long max 70", "filters", {"rsi_long_max": 70.0}),
        Variant("rsi_long_80", "RSI long max 80", "filters", {"rsi_long_max": 80.0}),
        Variant("rsi_short_40", "RSI short min 40", "filters", {"rsi_short_min": 40.0}),
        Variant("atr_1_2", "ATR stop ×1.2", "risk", {"atr_multiplier": 1.2}),
        Variant("atr_2_0", "ATR stop ×2.0", "risk", {"atr_multiplier": 2.0}),
        Variant("rr_1_5", "Min R:R 1.5", "risk", {"min_risk_reward_ratio": 1.5}),
        Variant("rr_2_5", "Min R:R 2.5", "risk", {"min_risk_reward_ratio": 2.5}),
        Variant("rr_3_0", "Min R:R 3.0", "risk", {"min_risk_reward_ratio": 3.0}),
        Variant("ist_entry", "IST entry (retest off)", "entry", {"retest_entry_enabled": False}),
        Variant(
            "retest_tight",
            "Retest 0.35-0.80 ATR",
            "entry",
            {"retest_zone_near": 0.35, "retest_zone_far": 0.80},
        ),
        Variant(
            "tp_tight",
            "TP 1.5/2.5/4.0R",
            "exits",
            {"tp_multipliers": (1.5, 2.5, 4.0)},
        ),
        Variant(
            "tp_wide",
            "TP 2.5/5.0/8.0R",
            "exits",
            {"tp_multipliers": (2.5, 5.0, 8.0)},
        ),
        Variant("scale_off", "No scale-out", "exits", {"scale_out_enabled": False}),
        Variant(
            "no_be",
            "No BE after TP1",
            "exits",
            {"move_stop_to_breakeven_after_tp1": False},
        ),
        Variant("expiry_12", "Expiry ×12", "exits", {"expiry_multiplier": 12}),
        Variant("expiry_48", "Expiry ×48", "exits", {"expiry_multiplier": 48}),
        Variant("cooldown_60", "Cooldown 60m", "exits", {"cooldown_minutes": 60}),
        Variant(
            "w_boost_trend",
            "+8pp trend",
            "weights",
            {"weights": _shift_weight(baseline_w, boost=ScoreCategory.TREND, delta=0.08)},
        ),
        Variant(
            "w_boost_momentum",
            "+8pp momentum",
            "weights",
            {"weights": _shift_weight(baseline_w, boost=ScoreCategory.MOMENTUM, delta=0.08)},
        ),
        Variant(
            "w_boost_volume",
            "+8pp volume",
            "weights",
            {"weights": _shift_weight(baseline_w, boost=ScoreCategory.VOLUME, delta=0.08)},
        ),
        Variant(
            "w_boost_structure",
            "+8pp structure",
            "weights",
            {
                "weights": _shift_weight(
                    baseline_w, boost=ScoreCategory.MARKET_STRUCTURE, delta=0.08
                )
            },
        ),
        Variant(
            "w_boost_volatility",
            "+4pp volatility",
            "weights",
            {"weights": _shift_weight(baseline_w, boost=ScoreCategory.VOLATILITY, delta=0.04)},
        ),
        Variant(
            "w_reduce_momentum",
            "-5pp momentum",
            "weights",
            {"weights": _shift_weight(baseline_w, boost=ScoreCategory.MOMENTUM, delta=-0.05)},
        ),
        Variant(
            "combo_quality",
            "Score78 + ADX25 + RR2.5",
            "combo",
            {"min_score": 78.0, "min_adx": 25.0, "min_risk_reward_ratio": 2.5},
        ),
        Variant(
            "combo_loose_flow",
            "Score70 + no STRONG + ADX15",
            "combo",
            {"min_score": 70.0, "require_strong_signals": False, "min_adx": 15.0},
        ),
        Variant(
            "combo_trend_ist",
            "IST + boost trend + ADX25",
            "combo",
            {
                "retest_entry_enabled": False,
                "min_adx": 25.0,
                "weights": _shift_weight(baseline_w, boost=ScoreCategory.TREND, delta=0.08),
            },
        ),
        # --- Profit-Hebel Combos (Post-Sweep) ---
        Variant(
            "ref_adx20",
            "Reference ADX20 (pre-change baseline)",
            "baseline",
            {"min_adx": 20.0},
        ),
        Variant(
            "combo_adx30_tp",
            "ADX30 + TP 1.5/2.5/4R",
            "combo",
            {"min_adx": 30.0, "tp_multipliers": (1.5, 2.5, 4.0)},
        ),
        Variant(
            "combo_adx30_tp_rr",
            "ADX30 + TP tight + RR2.5",
            "combo",
            {
                "min_adx": 30.0,
                "tp_multipliers": (1.5, 2.5, 4.0),
                "min_risk_reward_ratio": 2.5,
            },
        ),
        Variant(
            "combo_adx30_tp_mom",
            "ADX30 + TP tight + +8pp momentum",
            "combo",
            {
                "min_adx": 30.0,
                "tp_multipliers": (1.5, 2.5, 4.0),
                "weights": _shift_weight(baseline_w, boost=ScoreCategory.MOMENTUM, delta=0.08),
            },
        ),
        Variant(
            "combo_pf_stack",
            "ADX30 + TP tight + RR2.5 + +8pp momentum",
            "combo",
            {
                "min_adx": 30.0,
                "tp_multipliers": (1.5, 2.5, 4.0),
                "min_risk_reward_ratio": 2.5,
                "weights": _shift_weight(baseline_w, boost=ScoreCategory.MOMENTUM, delta=0.08),
            },
        ),
    ]


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if "error" not in r]
    failed = len(rows) - len(ok)
    with_trades = [r for r in ok if int(r["overall"]["trade_count"]) > 0]
    total_trades = sum(int(r["overall"]["trade_count"]) for r in ok)
    total_net = sum(float(r["overall"]["net_profit"]) for r in ok)
    total_fees = sum(float(r["overall"]["total_fees"]) for r in ok)
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
    }


def _base_config_kwargs(settings) -> dict[str, Any]:
    return {
        "timeframe": settings.primary_timeframe,
        "fee_percent": 0.05,
        "slippage_percent": 0.05,
        "initial_capital": 5_000.0,
        "min_score": settings.signal_min_score,
        "min_risk_reward_ratio": settings.min_risk_reward_ratio,
        "atr_multiplier": settings.atr_multiplier,
        "max_atr_percent": settings.max_atr_percent,
        "expiry_multiplier": settings.signal_expiry_multiplier,
        "timeframes": tuple(settings.timeframes),
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


def _run_symbol_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Process-worker: alle Varianten auf einem Symbol-DataFrame."""
    logging.disable(logging.INFO)
    symbol = payload["symbol"]
    rank = payload["rank"]
    timeframe = payload["timeframe"]
    base_kwargs = payload["base_kwargs"]
    # Reconstruct weights objects
    base_kwargs = dict(base_kwargs)
    weights_dump = base_kwargs.pop("weights")
    if isinstance(weights_dump, dict):
        base_kwargs["weights"] = StrategyWeights(**weights_dump)
    df = pd.DataFrame(payload["frame"])
    if "open_time" in df.columns:
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        df = df.set_index("open_time", drop=False)

    results: dict[str, dict[str, Any]] = {}
    for variant in payload["variants"]:
        key = variant["key"]
        overrides = dict(variant["overrides"])
        if "weights" in overrides and isinstance(overrides["weights"], dict):
            overrides["weights"] = StrategyWeights(**overrides["weights"])
        if "tp_multipliers" in overrides and isinstance(overrides["tp_multipliers"], list):
            overrides["tp_multipliers"] = tuple(overrides["tp_multipliers"])
        if "scale_out_fractions" in overrides and isinstance(
            overrides["scale_out_fractions"], list
        ):
            overrides["scale_out_fractions"] = tuple(overrides["scale_out_fractions"])
        try:
            config = BacktestConfig(symbol=symbol, **{**base_kwargs, **overrides})
            outcome = BacktestEngine(config).run(df)
            overall = _pick(compute_metrics(outcome).get("overall"))
            results[key] = {"symbol": symbol, "market_cap_rank": rank, "overall": overall}
        except Exception as exc:
            results[key] = {"symbol": symbol, "market_cap_rank": rank, "error": str(exc)}
    return {"symbol": symbol, "rank": rank, "results": results}


def _serialize_variant(v: Variant) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for key, value in v.overrides.items():
        if isinstance(value, StrategyWeights):
            overrides[key] = value.model_dump()
        elif isinstance(value, tuple):
            overrides[key] = list(value)
        else:
            overrides[key] = value
    return {"key": v.key, "label": v.label, "group": v.group, "overrides": overrides}


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
            # Ensure open_time column for pickle
            if "open_time" not in df.columns and df.index.name == "open_time":
                df = df.reset_index()
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


async def main() -> int:
    parser = argparse.ArgumentParser(description="Top-N strategy optimization sweep")
    parser.add_argument("--top", type=int, default=300)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--capital", type=float, default=5_000.0)
    parser.add_argument("--fee", type=float, default=0.05)
    parser.add_argument("--slippage", type=float, default=0.05)
    parser.add_argument("--workers", type=int, default=max(2, min(8, (os.cpu_count() or 4))))
    parser.add_argument("--only", default="", help="Comma-separated variant keys")
    parser.add_argument("--out", default="exports/optimize_top300_30d.json")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING", json_output=False)
    logging.getLogger().setLevel(logging.WARNING)
    container = build_container(settings)

    end = utc_now()
    start = end - timedelta(days=args.days)
    symbols = await _load_symbols(args.top)
    if not symbols:
        print("No universe symbols", file=sys.stderr)
        await container.aclose()
        return 1

    variants = _catalog()
    if args.only.strip():
        wanted = {k.strip() for k in args.only.split(",") if k.strip()}
        variants = [v for v in variants if v.key in wanted]
    ser_variants = [_serialize_variant(v) for v in variants]

    base_kwargs = _base_config_kwargs(settings)
    base_kwargs["timeframe"] = args.timeframe
    base_kwargs["fee_percent"] = args.fee
    base_kwargs["slippage_percent"] = args.slippage
    base_kwargs["initial_capital"] = args.capital
    # Make pickle-friendly
    base_kwargs["weights"] = base_kwargs["weights"].model_dump()
    base_kwargs["tp_multipliers"] = list(base_kwargs["tp_multipliers"])
    base_kwargs["scale_out_fractions"] = list(base_kwargs["scale_out_fractions"])
    base_kwargs["timeframes"] = list(base_kwargs["timeframes"])

    print(
        f"Optimize {len(symbols)} symbols · {args.days}d · {args.timeframe} · "
        f"variants={len(variants)} · workers={args.workers} · {start.date()} → {end.date()}",
        file=sys.stderr,
        flush=True,
    )

    t0 = time.time()
    try:
        loaded = await _load_all_frames(symbols, args.timeframe, start, end)
    finally:
        await container.aclose()

    per_variant_rows: dict[str, list[dict[str, Any]]] = {v.key: [] for v in variants}
    jobs: list[dict[str, Any]] = []
    for item in loaded:
        if "error" in item:
            for v in variants:
                per_variant_rows[v.key].append(
                    {
                        "symbol": item["symbol"],
                        "market_cap_rank": item["rank"],
                        "error": item["error"],
                    }
                )
            continue
        jobs.append(
            {
                "symbol": item["symbol"],
                "rank": item["rank"],
                "timeframe": args.timeframe,
                "base_kwargs": base_kwargs,
                "variants": ser_variants,
                "frame": item["frame"],
            }
        )

    print(f"Running {len(jobs)} symbols × {len(variants)} variants ...", file=sys.stderr, flush=True)
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_run_symbol_job, job): job["symbol"] for job in jobs}
        for fut in as_completed(futures):
            symbol = futures[fut]
            done += 1
            try:
                payload = fut.result()
                for key, row in payload["results"].items():
                    per_variant_rows[key].append(row)
            except Exception as exc:
                for v in variants:
                    per_variant_rows[v.key].append(
                        {"symbol": symbol, "market_cap_rank": 0, "error": str(exc)}
                    )
            if done % 10 == 0 or done == len(jobs):
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed else 0
                eta = (len(jobs) - done) / rate if rate else 0
                print(
                    f"[{done}/{len(jobs)}] last={symbol}  "
                    f"{elapsed / 60:.1f}m elapsed  ETA {eta / 60:.1f}m",
                    file=sys.stderr,
                    flush=True,
                )

    baseline_summary = (
        _aggregate(per_variant_rows["baseline"]) if "baseline" in per_variant_rows else {}
    )
    ranked: list[dict[str, Any]] = []
    for variant in variants:
        summary = _aggregate(per_variant_rows[variant.key])
        delta = None
        if baseline_summary:
            delta = round(
                float(summary["total_net_profit"]) - float(baseline_summary["total_net_profit"]),
                2,
            )
        ranked.append(
            {
                "key": variant.key,
                "label": variant.label,
                "group": variant.group,
                "overrides": _serialize_variant(variant)["overrides"],
                "summary": summary,
                "delta_vs_baseline": delta,
            }
        )

    ranked.sort(
        key=lambda row: (
            float(row["summary"]["total_net_profit"]),
            float(row["summary"]["avg_profit_factor"]),
            int(row["summary"]["total_trades"]),
        ),
        reverse=True,
    )

    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "type": "topn_db_variant_sweep_parallel",
            "top_n": args.top,
            "days": args.days,
            "timeframe": args.timeframe,
            "use_multi_timeframe": False,
            "workers": args.workers,
            "capital_per_symbol": args.capital,
            "fee_percent": args.fee,
            "slippage_percent": args.slippage,
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "variants": len(variants),
            "note": (
                "Per-symbol capital accounts are independent; "
                "total_net_profit ranks relative strategy quality."
            ),
        },
        "baseline": next((r for r in ranked if r["key"] == "baseline"), None),
        "ranked": ranked,
        "winners_vs_baseline": [
            r
            for r in ranked
            if r.get("delta_vs_baseline") is not None and float(r["delta_vs_baseline"]) > 0
        ],
        "elapsed_seconds": round(time.time() - t0, 1),
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "wrote": str(out_path),
                "elapsed_min": round((time.time() - t0) / 60, 1),
                "baseline_net": baseline_summary.get("total_net_profit"),
                "top5": [
                    {
                        "key": r["key"],
                        "net": r["summary"]["total_net_profit"],
                        "delta": r["delta_vs_baseline"],
                        "trades": r["summary"]["total_trades"],
                    }
                    for r in ranked[:5]
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    # Required on some platforms for ProcessPool
    raise SystemExit(asyncio.run(main()))
