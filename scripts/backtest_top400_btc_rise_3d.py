"""Top-400 × 3d paper-parity A/B: old strategy vs BTC-rise short pause.

Runs the same BacktestEngine paper-parity stack twice on universe top-N:
  old — btc_rise_short_block_enabled=False
  new — btc_rise_short_block_enabled=True (live defaults)

Portfolio caps match paper (max open / per direction). Reports $ PnL delta.

    PYTHONPATH=. python scripts/backtest_top400_btc_rise_3d.py --top 400 --days 3 --workers 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backtesting.engine import WARMUP_CANDLES  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.core.time import utc_now  # noqa: E402
from scripts.run_top400_paper_parity_90d import (  # noqa: E402
    _apply_paper_portfolio,
    _df_to_records,
    _load_frame,
    _load_symbols,
    _paper_config_kwargs,
    _run_symbol_job,
    _serialize_kwargs,
    _summarize,
)


def _run_variant(
    *,
    label: str,
    jobs_base: list[dict[str, Any]],
    base_kwargs: dict[str, Any],
    btc_rise: bool,
    workers: int,
    capital: float,
    max_open: int,
    max_per_direction: int,
) -> dict[str, Any]:
    kwargs = dict(base_kwargs)
    kwargs["btc_rise_short_block_enabled"] = bool(btc_rise)
    jobs = []
    for job in jobs_base:
        row = dict(job)
        row["base_kwargs"] = kwargs
        jobs.append(row)

    print(f"\n=== {label} (btc_rise={btc_rise}) · {len(jobs)} symbols ===", flush=True)
    t0 = time.time()
    per_symbol: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    done = 0
    workers = max(1, int(workers))

    def _consume(row: dict[str, Any]) -> None:
        nonlocal done
        done += 1
        if "error" in row:
            per_symbol.append(row)
        else:
            trades = row.pop("trades", [])
            per_symbol.append(row)
            all_trades.extend(trades)
        if done % 25 == 0 or done == len(jobs):
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed else 0
            eta_m = (len(jobs) - done) / rate / 60 if rate else 0
            print(
                f"  [{done}/{len(jobs)}] {row.get('symbol')} "
                f"trades={row.get('trade_count', 0)} · {elapsed/60:.1f}m ETA {eta_m:.0f}m",
                flush=True,
            )

    if workers == 1:
        for job in jobs:
            _consume(_run_symbol_job(job))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_symbol_job, job) for job in jobs]
            for fut in as_completed(futures):
                _consume(fut.result())

    accepted, skipped, curve = _apply_paper_portfolio(
        all_trades,
        start_equity=capital,
        max_open=max_open,
        max_per_direction=max_per_direction,
    )
    kpi = _summarize(accepted, start_equity=capital, curve=curve)
    errors = sum(1 for r in per_symbol if "error" in r)
    print(
        f"  done {label}: closed={kpi.get('closed')} pnl=${kpi.get('net_pnl'):.2f} "
        f"wr={float(kpi.get('win_rate') or 0):.1f}% · portfolio_skipped={skipped} errors={errors}",
        flush=True,
    )
    return {
        "label": label,
        "btc_rise": btc_rise,
        "symbols_ok": len(jobs),
        "symbols_error": errors,
        "portfolio_skipped": skipped,
        "kpi": kpi,
        "trades": accepted,
        "runtime_seconds": round(time.time() - t0, 1),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=400)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--capital", type=float, default=0.0)
    parser.add_argument(
        "--out",
        type=str,
        default=str(ROOT / "exports" / "top400_btc_rise_3d.json"),
    )
    args = parser.parse_args()

    configure_logging()
    logging.getLogger("app").setLevel(logging.WARNING)
    settings = get_settings()
    capital = float(args.capital or settings.paper_initial_balance or 5000.0)
    fee = float(settings.paper_fee_percent)
    end = utc_now()
    start = end - timedelta(days=int(args.days))
    base_kwargs = _serialize_kwargs(
        _paper_config_kwargs(settings, capital=capital, fee=fee, slip=0.0)
    )
    # Default old path: parity script historically had rise off until wired.
    base_kwargs.setdefault("btc_rise_short_block_enabled", False)
    for key, default in (
        ("btc_rise_1h_pct", settings.btc_rise_1h_pct),
        ("btc_rise_3h_pct", settings.btc_rise_3h_pct),
        ("btc_rise_4h_pct", settings.btc_rise_4h_pct),
        ("btc_rise_6h_pct", settings.btc_rise_6h_pct),
        ("btc_rise_use_1h", settings.btc_rise_use_1h),
        ("btc_rise_use_3h", settings.btc_rise_use_3h),
        ("btc_rise_use_4h", settings.btc_rise_use_4h),
        ("btc_rise_use_6h", settings.btc_rise_use_6h),
    ):
        base_kwargs.setdefault(key, default)

    symbols = await _load_symbols(args.top)
    print(
        f"Top{len(symbols)} × {args.days}d A/B · workers={args.workers} · "
        f"capital={capital} · {start.date()}→{end.date()}",
        flush=True,
    )

    print("Loading BTC 1h+4h ...", flush=True)
    btc_1h = await _load_frame(settings.regime_btc_symbol.upper(), "1h", start=start, end=end)
    btc_4h = await _load_frame(settings.regime_btc_symbol.upper(), "4h", start=start, end=end)
    btc_frames_ser: dict[str, list] = {}
    if btc_1h is not None:
        btc_frames_ser["1h"] = _df_to_records(btc_1h)
    if btc_4h is not None:
        btc_frames_ser["4h"] = _df_to_records(btc_4h)
    print(
        f"  BTC 1h={0 if btc_1h is None else len(btc_1h)} "
        f"4h={0 if btc_4h is None else len(btc_4h)}",
        flush=True,
    )

    print("Preloading top symbol 1h frames ...", flush=True)
    jobs_base: list[dict[str, Any]] = []
    load_failed = 0
    for idx, (symbol, rank) in enumerate(symbols, start=1):
        df = await _load_frame(symbol, "1h", start=start, end=end)
        if df is None or len(df) < WARMUP_CANDLES + 10:
            load_failed += 1
            continue
        jobs_base.append(
            {
                "symbol": symbol,
                "rank": rank,
                "frame": _df_to_records(df),
                "btc_frames": btc_frames_ser,
                "base_kwargs": base_kwargs,
            }
        )
        if idx % 50 == 0 or idx == len(symbols):
            print(
                f"  loaded {idx}/{len(symbols)} jobs={len(jobs_base)} fail={load_failed}",
                flush=True,
            )

    max_open = int(settings.paper_max_open_positions)
    max_dir = int(settings.paper_max_open_per_direction)

    old = _run_variant(
        label="OLD",
        jobs_base=jobs_base,
        base_kwargs=base_kwargs,
        btc_rise=False,
        workers=args.workers,
        capital=capital,
        max_open=max_open,
        max_per_direction=max_dir,
    )
    new = _run_variant(
        label="NEW",
        jobs_base=jobs_base,
        base_kwargs=base_kwargs,
        btc_rise=True,
        workers=args.workers,
        capital=capital,
        max_open=max_open,
        max_per_direction=max_dir,
    )

    old_pnl = float(old["kpi"].get("net_pnl") or 0.0)
    new_pnl = float(new["kpi"].get("net_pnl") or 0.0)
    delta = round(new_pnl - old_pnl, 2)

    def _side_pnl(trades: list[dict[str, Any]], short: bool) -> float:
        total = 0.0
        for t in trades:
            d = str(t.get("direction", "")).upper()
            is_short = "SHORT" in d
            if is_short == short:
                total += float(t.get("net_pnl") or 0.0)
        return round(total, 2)

    out = {
        "generated_at": utc_now().isoformat(),
        "window": {
            "days": int(args.days),
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "universe": {
            "requested": int(args.top),
            "loaded": len(jobs_base),
            "load_failed": load_failed,
        },
        "capital": capital,
        "gates": {
            "min_score": base_kwargs.get("min_score"),
            "short_max_score": base_kwargs.get("short_max_score"),
            "short_min_score": base_kwargs.get("short_min_score"),
            "regime_filter_enabled": base_kwargs.get("regime_filter_enabled"),
            "retest_trendline_gate": base_kwargs.get("retest_trendline_gate"),
            "btc_rise_thresholds": {
                k: base_kwargs.get(k)
                for k in (
                    "btc_rise_1h_pct",
                    "btc_rise_3h_pct",
                    "btc_rise_4h_pct",
                    "btc_rise_6h_pct",
                )
            },
        },
        "old": {
            "kpi": old["kpi"],
            "runtime_seconds": old["runtime_seconds"],
            "long_pnl": _side_pnl(old["trades"], short=False),
            "short_pnl": _side_pnl(old["trades"], short=True),
        },
        "new": {
            "kpi": new["kpi"],
            "runtime_seconds": new["runtime_seconds"],
            "long_pnl": _side_pnl(new["trades"], short=False),
            "short_pnl": _side_pnl(new["trades"], short=True),
        },
        "delta_pnl_usd_new_minus_old": delta,
        "delta_trades": int(new["kpi"].get("closed") or 0) - int(old["kpi"].get("closed") or 0),
    }
    # Drop heavy trade lists from disk summary (keep counts in kpi).
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("\n========== Top400 × 3d Alt vs Neu ==========")
    print(
        f"OLD: n={old['kpi'].get('closed')} pnl=${old_pnl:.2f} "
        f"(L ${out['old']['long_pnl']:.2f} / S ${out['old']['short_pnl']:.2f}) "
        f"wr={float(old['kpi'].get('win_rate') or 0):.1f}%"
    )
    print(
        f"NEW: n={new['kpi'].get('closed')} pnl=${new_pnl:.2f} "
        f"(L ${out['new']['long_pnl']:.2f} / S ${out['new']['short_pnl']:.2f}) "
        f"wr={float(new['kpi'].get('win_rate') or 0):.1f}%"
    )
    print(f"DIFF (NEW − OLD): ${delta:+.2f}")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
