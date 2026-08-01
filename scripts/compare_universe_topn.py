#!/usr/bin/env python3
"""Vergleich Top-300 vs Top-500 (Baseline, Live-Gates) nach market_cap_rank.

Laedt Symbole per Rank (ohne in_universe-Filter), spielt einmal Baseline
auf Top-N, aggregiert Buckets 1-300 / 301-500 / 1-500.

  python scripts/compare_universe_topn.py --top 500 --days 30 --workers 2 \\
    --out exports/universe_300_vs_500_30d.json
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
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

# Allow running as `python scripts/...` from repo root.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.container import build_container
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.time import utc_now
from app.database.session import session_scope
from app.models.market import Asset
# Import helpers from sibling module (repo root on PYTHONPATH / Docker /app).
import importlib.util

_opt_path = Path(__file__).resolve().parent / "optimize_strategy_top300.py"
_spec = importlib.util.spec_from_file_location("optimize_strategy_top300", _opt_path)
assert _spec and _spec.loader
_opt = importlib.util.module_from_spec(_spec)
sys.modules["optimize_strategy_top300"] = _opt
_spec.loader.exec_module(_opt)

_aggregate = _opt._aggregate
_base_config_kwargs = _opt._base_config_kwargs
_load_all_frames = _opt._load_all_frames
_run_symbol_job = _opt._run_symbol_job


async def _aload_symbols_by_rank(limit: int) -> list[tuple[str, int]]:
    """Erste ``limit`` Universe-Coins nach MCAP-Rank (Rank darf >limit sein)."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Asset.symbol, Asset.market_cap_rank)
                .where(
                    Asset.is_active.is_(True),
                    Asset.in_universe.is_(True),
                    Asset.market_cap_rank.is_not(None),
                )
                .order_by(Asset.market_cap_rank.asc(), Asset.symbol)
                .limit(limit)
            )
        ).all()
    return [(str(symbol).upper(), int(rank)) for symbol, rank in rows]


def _bucket_rows(rows: list[dict[str, Any]], lo: int, hi: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        rank = int(row.get("market_cap_rank") or 0)
        if lo <= rank <= hi:
            out.append(row)
    return out


def _expectancy_usd(summary: dict[str, Any]) -> float:
    trades = int(summary.get("total_trades") or 0)
    if trades <= 0:
        return 0.0
    return round(float(summary["total_net_profit"]) / trades, 4)


def _enrich(summary: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(summary)
    enriched["expectancy_usd"] = _expectancy_usd(summary)
    ok = int(summary.get("symbols_ok") or 0)
    profitable = int(summary.get("symbols_profitable") or 0)
    enriched["profitable_symbol_pct"] = round(100.0 * profitable / ok, 1) if ok else 0.0
    return enriched


def _recommendation(buckets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    mid = buckets["301-500"]
    core = buckets["1-300"]
    mid_exp = float(mid["expectancy_usd"])
    core_exp = float(core["expectancy_usd"])
    mid_pf = float(mid["avg_profit_factor"])
    core_pf = float(core["avg_profit_factor"])
    mid_net = float(mid["total_net_profit"])
    mid_trades = int(mid["total_trades"])

    reasons: list[str] = []
    keep_300 = False

    if mid_trades < 10:
        keep_300 = True
        reasons.append(
            f"Slice 301-500 hat zu wenige Trades (n={mid_trades}) fuer eine belastbare Aussage."
        )
    if mid_exp < core_exp - 0.5:
        keep_300 = True
        reasons.append(
            f"Expectancy 301-500 ({mid_exp:+.2f}$/Trade) liegt klar unter 1-300 ({core_exp:+.2f}$)."
        )
    if mid_pf > 0 and core_pf > 0 and mid_pf < core_pf * 0.9:
        keep_300 = True
        reasons.append(
            f"Profit-Faktor 301-500 ({mid_pf:.2f}) ist schwaecher als 1-300 ({core_pf:.2f})."
        )
    if mid_net < 0 and mid_exp <= 0:
        keep_300 = True
        reasons.append(f"Incremental Slice 301-500 ist netto negativ ({mid_net:+.0f}$).")

    if not keep_300 and mid_trades >= 10 and mid_exp >= core_exp * 0.85 and mid_pf >= core_pf * 0.9:
        decision = "consider_500"
        reasons.append(
            "301-500 ist in Expectancy/PF mit 1-300 vergleichbar — Expansion auf 500 ist pruefenswert."
        )
        reasons.append(
            "Ops: bei Batch 300 braucht Full-Coverage 500 etwa zwei Scan-Zyklen (~30m)."
        )
    else:
        decision = "keep_300"
        if not reasons:
            reasons.append("Kein klarer Profit-Vorteil fuer Ranks 301-500 erkennbar.")
        reasons.append("Empfehlung: UNIVERSE_TARGET_COUNT bei 300 belassen.")

    return {
        "decision": decision,
        "headline": (
            "Top-500 erwägen"
            if decision == "consider_500"
            else "Bei Top-300 bleiben"
        ),
        "reasons": reasons,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Universe Top-300 vs Top-500 baseline compare")
    parser.add_argument("--top", type=int, default=500)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--capital", type=float, default=5_000.0)
    parser.add_argument("--fee", type=float, default=0.05)
    parser.add_argument("--slippage", type=float, default=0.05)
    parser.add_argument("--workers", type=int, default=max(2, min(4, (os.cpu_count() or 4))))
    parser.add_argument("--out", default="exports/universe_300_vs_500_30d.json")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING", json_output=False)
    logging.getLogger().setLevel(logging.WARNING)
    container = build_container(settings)

    end = utc_now()
    start = end - timedelta(days=args.days)
    symbols = await _aload_symbols_by_rank(args.top)
    if not symbols:
        print("No ranked active symbols", file=sys.stderr)
        await container.aclose()
        return 1

    # Single baseline variant matching live defaults.
    baseline_variant = {
        "key": "baseline",
        "label": "Current live defaults",
        "group": "baseline",
        "overrides": {},
    }
    ser_variants = [baseline_variant]

    base_kwargs = _base_config_kwargs(settings)
    base_kwargs["timeframe"] = args.timeframe
    base_kwargs["fee_percent"] = args.fee
    base_kwargs["slippage_percent"] = args.slippage
    base_kwargs["initial_capital"] = args.capital
    base_kwargs["weights"] = base_kwargs["weights"].model_dump()
    base_kwargs["tp_multipliers"] = list(base_kwargs["tp_multipliers"])
    base_kwargs["scale_out_fractions"] = list(base_kwargs["scale_out_fractions"])
    base_kwargs["timeframes"] = list(base_kwargs["timeframes"])

    print(
        f"Compare universe ranks 1-{args.top} · {args.days}d · {args.timeframe} · "
        f"baseline-only · workers={args.workers} · in_universe top-N by rank · "
        f"{start.date()} → {end.date()} · symbols={len(symbols)}",
        file=sys.stderr,
        flush=True,
    )

    t0 = time.time()
    try:
        loaded = await _load_all_frames(symbols, args.timeframe, start, end)
    finally:
        await container.aclose()

    rows: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    for item in loaded:
        if "error" in item:
            rows.append(
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

    print(f"Running {len(jobs)} symbols (baseline) ...", file=sys.stderr, flush=True)
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_run_symbol_job, job): job["symbol"] for job in jobs}
        for fut in as_completed(futures):
            symbol = futures[fut]
            done += 1
            try:
                payload = fut.result()
                row = payload["results"]["baseline"]
                rows.append(row)
            except Exception as exc:
                rows.append({"symbol": symbol, "market_cap_rank": 0, "error": str(exc)})
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

    # Rank-buckets plus full loaded universe (ranks may exceed 500 via fill-down).
    buckets_raw = {
        "1-300": _bucket_rows(rows, 1, 300),
        "301-500": _bucket_rows(rows, 301, 500),
        "1-500": _bucket_rows(rows, 1, 500),
        "universe-top": [r for r in rows if "error" not in r],
    }
    buckets = {name: _enrich(_aggregate(bucket_rows)) for name, bucket_rows in buckets_raw.items()}
    recommendation = _recommendation(buckets)

    # Compact per-symbol for canvas / drilldown (no candle frames).
    symbols_out = []
    for row in sorted(rows, key=lambda r: int(r.get("market_cap_rank") or 10**9)):
        if "error" in row:
            symbols_out.append(
                {
                    "symbol": row["symbol"],
                    "rank": row.get("market_cap_rank"),
                    "error": row["error"],
                }
            )
            continue
        overall = row["overall"]
        symbols_out.append(
            {
                "symbol": row["symbol"],
                "rank": row.get("market_cap_rank"),
                "trades": int(overall["trade_count"]),
                "net": round(float(overall["net_profit"]), 2),
                "wr": round(float(overall["win_rate"]), 4),
                "pf": round(float(overall["profit_factor"]), 4)
                if math.isfinite(float(overall["profit_factor"]))
                else None,
            }
        )

    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "type": "universe_rank_bucket_baseline",
            "top_n": args.top,
            "days": args.days,
            "timeframe": args.timeframe,
            "by_rank": True,
            "in_universe_filter": False,
            "variant": "baseline",
            "workers": args.workers,
            "capital_per_symbol": args.capital,
            "fee_percent": args.fee,
            "slippage_percent": args.slippage,
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "symbols_requested": len(symbols),
            "symbols_with_candles": len(jobs),
            "note": (
                "Per-symbol $5k books are independent; totals are relative quality, "
                "not a true portfolio. Live gates from worker settings."
            ),
        },
        "buckets": buckets,
        "recommendation": recommendation,
        "symbols": symbols_out,
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
                "buckets": {
                    name: {
                        "net": b["total_net_profit"],
                        "trades": b["total_trades"],
                        "exp": b["expectancy_usd"],
                        "pf": b["avg_profit_factor"],
                        "ok": b["symbols_ok"],
                    }
                    for name, b in buckets.items()
                },
                "recommendation": recommendation,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
