"""Top400 × 7d paper-parity sim across entry-mode variants.

Preloads candles once. Each worker runs ALL variants per symbol (one frame
pickle), then the parent builds paper-book KPIs + equity per variant.

    python scripts/run_entry_variants_top400_7d.py --top 400 --days 7 --workers 2 \\
        --out /tmp/entry_variants_top400_7d.json
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.core.time import utc_now  # noqa: E402


def _load_parity_helpers() -> Any:
    path = ROOT / "scripts" / "run_top400_paper_parity_90d.py"
    spec = importlib.util.spec_from_file_location("run_top400_paper_parity_90d", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_parity = _load_parity_helpers()
BTC_WEIGHT_PRESETS = _parity.BTC_WEIGHT_PRESETS
_apply_paper_portfolio = _parity._apply_paper_portfolio
_df_to_records = _parity._df_to_records
_load_frame = _parity._load_frame
_load_symbols = _parity._load_symbols
_paper_config_kwargs = _parity._paper_config_kwargs
_serialize_kwargs = _parity._serialize_kwargs
_summarize = _parity._summarize
_write_checkpoint = _parity._write_checkpoint

VARIANTS: list[dict[str, Any]] = [
    {
        "key": "baseline",
        "label": "Baseline Retest",
        "overrides": {
            "retest_entry_enabled": True,
            "entry_mode": "retest",
            "retest_zone_near": 0.55,
            "retest_pending_multiplier": 6,
        },
    },
    {
        "key": "A_hybrid_chase",
        "label": "A Hybrid Chase (≥0.5 ATR)",
        "overrides": {
            "retest_entry_enabled": True,
            "entry_mode": "hybrid_chase",
            "chase_min_atr": 0.5,
            "retest_zone_near": 0.55,
            "retest_pending_multiplier": 6,
        },
    },
    {
        "key": "B_score_ist",
        "label": "B Score-Gate IST (≤22 / ≥85)",
        "overrides": {
            "retest_entry_enabled": True,
            "entry_mode": "score_ist",
            "score_ist_short_max": 22.0,
            "score_ist_long_min": 85.0,
            "retest_zone_near": 0.55,
            "retest_pending_multiplier": 6,
        },
    },
    {
        "key": "C_impulse_ist",
        "label": "C Impulse-Confirm IST",
        "overrides": {
            "retest_entry_enabled": True,
            "entry_mode": "impulse_ist",
            "impulse_min_adx": 35.0,
            "impulse_body_atr": 0.8,
            "retest_zone_near": 0.55,
            "retest_pending_multiplier": 6,
        },
    },
    {
        "key": "D_zone_near",
        "label": "D Zone nearer (0.35 ATR)",
        "overrides": {
            "retest_entry_enabled": True,
            "entry_mode": "retest",
            "retest_zone_near": 0.35,
            "retest_pending_multiplier": 6,
        },
    },
    {
        "key": "E_pending_10",
        "label": "E Pending ×10",
        "overrides": {
            "retest_entry_enabled": True,
            "entry_mode": "retest",
            "retest_zone_near": 0.55,
            "retest_pending_multiplier": 10,
        },
    },
    {
        "key": "E_pending_12",
        "label": "E Pending ×12",
        "overrides": {
            "retest_entry_enabled": True,
            "entry_mode": "retest",
            "retest_zone_near": 0.55,
            "retest_pending_multiplier": 12,
        },
    },
    {
        "key": "IST_only",
        "label": "IST only (retest off)",
        "overrides": {
            "retest_entry_enabled": False,
            "entry_mode": "ist",
        },
    },
]


def _worker_multi(payload: dict[str, Any]) -> dict[str, Any]:
    """Run every variant for one symbol (frame pickled once)."""
    logging.disable(logging.CRITICAL)
    symbol = payload["symbol"]
    rank = payload["rank"]
    base = payload["base_kwargs"]
    by_variant: dict[str, Any] = {}
    for variant in payload["variants"]:
        job = {
            "symbol": symbol,
            "rank": rank,
            "frame": payload["frame"],
            "btc_frames": payload["btc_frames"],
            "btc_tf_weights": payload.get("btc_tf_weights"),
            "base_kwargs": {**base, **variant["overrides"]},
        }
        by_variant[variant["key"]] = _parity._run_symbol_job(job)
    return {"symbol": symbol, "rank": rank, "by_variant": by_variant}


def _pack_variant_result(
    *,
    variant: dict[str, Any],
    all_trades: list[dict[str, Any]],
    capital: float,
    max_open: int,
    max_per_direction: int,
    symbol_errors: int,
) -> dict[str, Any]:
    accepted, skipped, curve = _apply_paper_portfolio(
        all_trades,
        start_equity=capital,
        max_open=max_open,
        max_per_direction=max_per_direction,
    )
    kpi = _summarize(accepted, start_equity=capital, curve=curve)
    return {
        "key": variant["key"],
        "label": variant["label"],
        "overrides": dict(variant["overrides"]),
        "kpi": kpi,
        "independent": {
            "raw_trades": len(all_trades),
            "accepted_trades": len(accepted),
            "skipped_by_caps": len(skipped),
            "raw_net_pnl": round(sum(float(t["net_pnl"]) for t in all_trades), 2),
            "symbol_errors": symbol_errors,
        },
        "equity_daily": kpi.get("equity_daily") or [],
        "equity_curve_sample": curve[:: max(1, len(curve) // 80)] if curve else [],
        "skip_reasons": {
            k: sum(1 for s in skipped if s.get("skip_reason") == k)
            for k in ("max_open", "max_per_direction")
        },
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=400)
    parser.add_argument("--days", type=float, default=7)
    parser.add_argument("--capital", type=float, default=0.0)
    parser.add_argument("--fee", type=float, default=-1.0)
    parser.add_argument("--slippage", type=float, default=0.0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--btc-weights", type=str, default="current", choices=("current", "old", "new"))
    parser.add_argument(
        "--variants",
        type=str,
        default="",
        help="Comma-separated variant keys (default: all)",
    )
    parser.add_argument("--out", type=str, default="exports/entry_variants_top400_7d.json")
    args = parser.parse_args()

    configure_logging()
    logging.getLogger("app").setLevel(logging.CRITICAL)
    logging.disable(logging.INFO)
    settings = get_settings()
    capital = float(args.capital or settings.paper_initial_balance or 5000.0)
    fee = float(settings.paper_fee_percent if args.fee < 0 else args.fee)
    end = utc_now()
    start = end - timedelta(days=float(args.days))
    btc_tf_weights = BTC_WEIGHT_PRESETS.get(str(args.btc_weights))
    base_kwargs = _serialize_kwargs(
        _paper_config_kwargs(settings, capital=capital, fee=fee, slip=float(args.slippage))
    )
    base_kwargs["retest_trendline_gate"] = False

    keys = {k.strip() for k in str(args.variants).split(",") if k.strip()}
    variants = [v for v in VARIANTS if not keys or v["key"] in keys]
    if not variants:
        print(f"No variants matched: {args.variants}", file=sys.stderr)
        return 2
    # Lightweight variant descriptors for workers (no huge objects)
    variant_payload = [{"key": v["key"], "overrides": v["overrides"]} for v in variants]

    symbols = await _load_symbols(args.top)
    t0 = time.time()
    print(
        f"Entry variants Top{len(symbols)} × {args.days}d · {len(variants)} variants/symbol · "
        f"workers={args.workers} · capital={capital}",
        file=sys.stderr,
        flush=True,
    )

    btc_tfs = tuple(
        tf.strip()
        for tf in str(getattr(settings, "market_regime_btc_timeframes", "1h,4h,1d,1w")).split(",")
        if tf.strip()
    ) or ("1h", "4h", "1d", "1w")
    btc_frames_ser: dict[str, list[dict[str, Any]]] = {}
    for tf in btc_tfs:
        btc_df = await _load_frame(settings.regime_btc_symbol.upper(), tf, start=start, end=end)
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
        if df is None or len(df) < 220:
            load_failed += 1
            continue
        jobs.append(
            {
                "symbol": symbol,
                "rank": rank,
                "frame": _df_to_records(df),
                "btc_frames": btc_frames_ser,
                "btc_tf_weights": btc_tf_weights,
                "base_kwargs": base_kwargs,
                "variants": variant_payload,
            }
        )
        if idx % 50 == 0 or idx == len(symbols):
            print(f"  loaded {idx}/{len(symbols)} (jobs={len(jobs)})", file=sys.stderr, flush=True)

    trades_by_variant: dict[str, list[dict[str, Any]]] = {v["key"]: [] for v in variants}
    errors_by_variant: dict[str, int] = {v["key"]: 0 for v in variants}
    done = 0
    workers = max(1, int(args.workers))
    out = Path(args.out)
    print(f"Simulating {len(jobs)} symbols × {len(variants)} variants...", file=sys.stderr, flush=True)

    def _consume(row: dict[str, Any]) -> None:
        nonlocal done
        done += 1
        by_v = row.get("by_variant") or {}
        for v in variants:
            key = v["key"]
            sub = by_v.get(key) or {"error": "missing"}
            if "error" in sub:
                errors_by_variant[key] += 1
            else:
                trades_by_variant[key].extend(sub.get("trades") or [])
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed else 0
        eta_m = (len(jobs) - done) / rate / 60 if rate else 0
        if done <= 3 or done % 5 == 0 or done == len(jobs):
            print(
                f"[{done}/{len(jobs)}] last={row.get('symbol')} · {elapsed/60:.1f}m · ETA {eta_m:.0f}m",
                file=sys.stderr,
                flush=True,
            )
        # Partial ranking checkpoint
        if done <= 3 or done % 5 == 0 or done == len(jobs):
            partial_results = []
            for v in variants:
                packed = _pack_variant_result(
                    variant=v,
                    all_trades=trades_by_variant[v["key"]],
                    capital=capital,
                    max_open=int(settings.paper_max_open_positions),
                    max_per_direction=int(settings.paper_max_open_per_direction),
                    symbol_errors=errors_by_variant[v["key"]],
                )
                partial_results.append(packed)
            ranked = sorted(
                partial_results, key=lambda r: float(r["kpi"]["end_equity"]), reverse=True
            )
            _write_checkpoint(
                out.with_suffix(".partial.json"),
                {
                    "partial": True,
                    "done_symbols": done,
                    "total_symbols": len(jobs),
                    "ranking": [
                        {
                            "key": r["key"],
                            "end_equity": r["kpi"]["end_equity"],
                            "net_pnl": r["kpi"]["net_pnl"],
                            "closed": r["kpi"]["closed"],
                            "win_rate": r["kpi"]["win_rate"],
                            "profit_factor": r["kpi"]["profit_factor"],
                            "max_drawdown_pct": r["kpi"]["max_drawdown_pct"],
                        }
                        for r in ranked
                    ],
                    "variants": [
                        {
                            "key": r["key"],
                            "label": r["label"],
                            "kpi": r["kpi"],
                            "equity_daily": r.get("equity_daily") or [],
                        }
                        for r in partial_results
                    ],
                },
            )

    if workers <= 1:
        for job in jobs:
            _consume(_worker_multi(job))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_worker_multi, job): job["symbol"] for job in jobs}
            for fut in as_completed(futs):
                try:
                    _consume(fut.result())
                except Exception as exc:  # noqa: BLE001
                    _consume(
                        {
                            "symbol": futs[fut],
                            "rank": 0,
                            "by_variant": {
                                v["key"]: {"error": str(exc)} for v in variants
                            },
                        }
                    )

    results = [
        _pack_variant_result(
            variant=v,
            all_trades=trades_by_variant[v["key"]],
            capital=capital,
            max_open=int(settings.paper_max_open_positions),
            max_per_direction=int(settings.paper_max_open_per_direction),
            symbol_errors=errors_by_variant[v["key"]],
        )
        for v in variants
    ]
    ranked = sorted(results, key=lambda r: float(r["kpi"]["end_equity"]), reverse=True)
    payload = {
        "generated_at": utc_now().isoformat(),
        "label": "entry_variants_top400_7d",
        "runtime_seconds": round(time.time() - t0, 1),
        "window": {
            "days": float(args.days),
            "start": start.astimezone(timezone.utc).isoformat(),
            "end": end.astimezone(timezone.utc).isoformat(),
        },
        "config": {
            "top_n": len(symbols),
            "jobs": len(jobs),
            "load_failed": load_failed,
            "workers": workers,
            "capital": capital,
            "fee_percent": fee,
            "slippage_percent": float(args.slippage),
            "btc_weights": args.btc_weights,
            "btc_tf_weights": btc_tf_weights,
            "paper_max_open_positions": settings.paper_max_open_positions,
            "paper_max_open_per_direction": settings.paper_max_open_per_direction,
            "signal_min_score": settings.signal_min_score,
            "signal_short_min_score": settings.signal_short_min_score,
            "signal_short_max_score": settings.signal_short_max_score,
            "retest_trendline_gate": False,
            "note": "Paper-parity book caps; all entry_mode variants per symbol in one worker job.",
        },
        "ranking": [
            {
                "key": r["key"],
                "label": r["label"],
                "end_equity": r["kpi"]["end_equity"],
                "net_pnl": r["kpi"]["net_pnl"],
                "return_pct": r["kpi"]["return_pct"],
                "closed": r["kpi"]["closed"],
                "win_rate": r["kpi"]["win_rate"],
                "profit_factor": r["kpi"]["profit_factor"],
                "max_drawdown_pct": r["kpi"]["max_drawdown_pct"],
            }
            for r in ranked
        ],
        "variants": results,
    }
    _write_checkpoint(out, payload)
    print(json.dumps({"wrote": str(out), "ranking": payload["ranking"]}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        import multiprocessing as mp

        if mp.get_start_method(allow_none=True) is None:
            mp.set_start_method("fork")
    except RuntimeError:
        pass
    raise SystemExit(asyncio.run(main()))
