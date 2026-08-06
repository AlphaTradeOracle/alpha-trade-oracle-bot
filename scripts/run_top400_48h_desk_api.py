#!/usr/bin/env python3
"""Top-N × N-days paper-parity backtest using public desk candles (no local DB).

Mirrors scripts/run_top400_paper_parity_90d.py KPIs, but loads universe + OHLCV
from https://alpha-trade-oracle.com when DATABASE is unavailable.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_top400_paper_parity_90d import (  # noqa: E402
    BTC_WEIGHT_PRESETS,
    _apply_paper_portfolio,
    _df_to_records,
    _paper_config_kwargs,
    _run_symbol_job,
    _serialize_kwargs,
    _summarize,
    _write_checkpoint,
)
from app.backtesting.engine import WARMUP_CANDLES  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.core.time import utc_now  # noqa: E402

BASE = "https://alpha-trade-oracle.com/api/v1"


def _get_json(url: str, *, retries: int = 3) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=90) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def _load_universe(top: int) -> list[tuple[str, int]]:
    rows = _get_json(f"{BASE}/assets?limit=500")
    uni = [
        r
        for r in rows
        if r.get("in_universe")
        and r.get("is_active")
        and r.get("market_cap_rank") is not None
        and str(r.get("symbol", "")).endswith("USDT")
    ]
    # de-dupe by symbol keeping best (lowest) rank
    best: dict[str, dict[str, Any]] = {}
    for r in uni:
        sym = str(r["symbol"]).upper()
        prev = best.get(sym)
        if prev is None or int(r["market_cap_rank"]) < int(prev["market_cap_rank"]):
            best[sym] = r
    ordered = sorted(best.values(), key=lambda r: int(r["market_cap_rank"]))[:top]
    return [(str(r["symbol"]).upper(), int(r["market_cap_rank"])) for r in ordered]


def _interval_delta(interval: str) -> timedelta:
    mapping = {
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
        "1w": timedelta(days=7),
        "15m": timedelta(minutes=15),
    }
    if interval not in mapping:
        raise ValueError(f"unsupported interval {interval}")
    return mapping[interval]


def _fetch_candles_df(
    symbol: str,
    interval: str,
    *,
    start: datetime,
    end: datetime,
) -> pd.DataFrame | None:
    # Match DB loader: warmup_start = start - tf * WARMUP so bar[WARMUP] ≈ start.
    step = _interval_delta(interval)
    warmup_start = start - step * WARMUP_CANDLES
    fr = int((warmup_start - step * 2).timestamp())
    to = int((end + step * 2).timestamp())
    data = _get_json(
        f"{BASE}/desk/candles?symbol={symbol}&interval={interval}&from={fr}&to={to}&limit=1000"
    )
    if not data:
        return None
    rows = []
    for c in data:
        ot = datetime.fromtimestamp(int(c["time"]), tz=timezone.utc)
        rows.append(
            {
                "open_time": ot,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c.get("volume") or 0.0),
            }
        )
    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time")
    df = df.set_index("open_time", drop=True)
    df.index = pd.DatetimeIndex(df.index, name="open_time")
    # Keep only warmup→end so the engine's first tradeable bar is near ``start``.
    df = df.loc[(df.index >= warmup_start - step) & (df.index <= end + step)]
    return df if not df.empty else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=400)
    parser.add_argument("--days", type=float, default=2.0)
    parser.add_argument(
        "--since",
        type=str,
        default="",
        help="ISO start UTC; with --until fixes the window for A/B runs",
    )
    parser.add_argument(
        "--until",
        type=str,
        default="",
        help="ISO end UTC; defaults to now when omitted",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--fetch-workers", type=int, default=12)
    parser.add_argument("--capital", type=float, default=0.0)
    parser.add_argument("--fee", type=float, default=-1.0)
    parser.add_argument("--slippage", type=float, default=0.0)
    parser.add_argument("--btc-weights", type=str, default="current", choices=("current", "old", "new"))
    parser.add_argument("--out", type=str, default="exports/top400_48h_api.json")
    parser.add_argument("--label", type=str, default="")
    args = parser.parse_args()

    configure_logging()
    logging.getLogger("app").setLevel(logging.WARNING)
    settings = get_settings()
    capital = float(args.capital or settings.paper_initial_balance or 5000.0)
    fee = float(settings.paper_fee_percent if args.fee < 0 else args.fee)
    if str(args.until).strip():
        end = datetime.fromisoformat(str(args.until).replace("Z", "+00:00"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        else:
            end = end.astimezone(timezone.utc)
    else:
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

    symbols = _load_universe(args.top)
    t0 = time.time()
    print(
        f"API Top{len(symbols)} paper-parity · days={args.days} · "
        f"score>={settings.signal_min_score} adx>={settings.signal_min_adx} "
        f"rsi_short>={settings.signal_rsi_short_min} · "
        f"workers={args.workers} · {start.isoformat()}→{end.isoformat()}",
        file=sys.stderr,
        flush=True,
    )

    btc_tfs = tuple(
        tf.strip()
        for tf in str(
            getattr(settings, "market_regime_btc_timeframes", "1h,4h,1d,1w")
        ).split(",")
        if tf.strip()
    ) or ("1h", "4h", "1d", "1w")
    btc_sym = str(settings.regime_btc_symbol).upper()
    print(f"Loading BTC regime TFs {btc_tfs}...", file=sys.stderr, flush=True)
    btc_frames_ser: dict[str, list[dict[str, Any]]] = {}
    for tf in btc_tfs:
        btc_df = _fetch_candles_df(btc_sym, tf, start=start, end=end)
        if btc_df is not None:
            btc_frames_ser[tf] = _df_to_records(btc_df)
        print(
            f"  BTC {tf} bars={0 if btc_df is None else len(btc_df)}",
            file=sys.stderr,
            flush=True,
        )

    print("Preloading 1h frames via desk API...", file=sys.stderr, flush=True)
    jobs: list[dict[str, Any]] = []
    load_failed = 0

    def _one(item: tuple[str, int]) -> dict[str, Any] | None:
        symbol, rank = item
        try:
            df = _fetch_candles_df(symbol, "1h", start=start, end=end)
        except Exception as exc:  # noqa: BLE001
            return {"symbol": symbol, "rank": rank, "error": str(exc)}
        if df is None or len(df) < WARMUP_CANDLES + 10:
            return None
        return {
            "symbol": symbol,
            "rank": rank,
            "frame": _df_to_records(df),
            "btc_frames": btc_frames_ser,
            "base_kwargs": base_kwargs,
            "btc_tf_weights": btc_tf_weights,
        }

    with ThreadPoolExecutor(max_workers=max(1, int(args.fetch_workers))) as pool:
        futs = {pool.submit(_one, item): item[0] for item in symbols}
        done_fetch = 0
        for fut in as_completed(futs):
            done_fetch += 1
            row = fut.result()
            if row is None:
                load_failed += 1
            elif "error" in row and "frame" not in row:
                load_failed += 1
            else:
                jobs.append(row)
            if done_fetch % 50 == 0 or done_fetch == len(symbols):
                print(
                    f"  fetched {done_fetch}/{len(symbols)} (jobs={len(jobs)} fail={load_failed})",
                    file=sys.stderr,
                    flush=True,
                )

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
        if done % 25 == 0 or done == len(jobs):
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed else 0
            eta_m = (len(jobs) - done) / rate / 60 if rate else 0
            print(
                f"[{done}/{len(jobs)}] last={row.get('symbol')} "
                f"trades={row.get('trade_count', 0)} · {elapsed/60:.1f}m · ETA {eta_m:.0f}m",
                file=sys.stderr,
                flush=True,
            )
            accepted, skipped, curve = _apply_paper_portfolio(
                all_trades,
                start_equity=capital,
                max_open=int(settings.paper_max_open_positions),
                max_per_direction=int(settings.paper_max_open_per_direction),
            )
            kpi = _summarize(accepted, start_equity=capital, curve=curve)
            _write_checkpoint(
                Path(args.out).with_name(Path(args.out).stem + ".partial.json"),
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
                        "signal_min_score": settings.signal_min_score,
                        "signal_min_adx": settings.signal_min_adx,
                        "signal_rsi_short_min": settings.signal_rsi_short_min,
                        "signal_short_max_score": settings.signal_short_max_score,
                    },
                    "kpi_paper_book": kpi,
                    "independent": {
                        "raw_trades": len(all_trades),
                        "accepted_trades": len(accepted),
                        "skipped_by_caps": len(skipped),
                        "raw_net_pnl": round(sum(float(t["net_pnl"]) for t in all_trades), 2),
                    },
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

    # Safety: drop any fill before the requested window (warmup alignment drift).
    start_iso = start.astimezone(timezone.utc).isoformat()
    all_trades_window = [t for t in all_trades if str(t.get("entry_at") or "") >= start_iso]
    accepted, skipped, curve = _apply_paper_portfolio(
        all_trades_window,
        start_equity=capital,
        max_open=int(settings.paper_max_open_positions),
        max_per_direction=int(settings.paper_max_open_per_direction),
    )
    kpi = _summarize(accepted, start_equity=capital, curve=curve)
    uncapped_net = sum(float(t["net_pnl"]) for t in all_trades_window)
    with_trades = [r for r in per_symbol if "error" not in r and int(r.get("trade_count", 0)) > 0]
    top_winners = sorted(with_trades, key=lambda r: float(r["net_profit"]), reverse=True)[:15]
    top_losers = sorted(with_trades, key=lambda r: float(r["net_profit"]))[:15]
    failed = sum(1 for r in per_symbol if "error" in r) + load_failed

    payload = {
        "generated_at": utc_now().isoformat(),
        "label": str(args.label).strip()
        or (
            f"top400_48h_api_score{settings.signal_min_score:g}"
            f"_adx{settings.signal_min_adx:g}"
            f"_rsiS{settings.signal_rsi_short_min:g}"
        ),
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
            "signal_min_adx": settings.signal_min_adx,
            "signal_rsi_short_min": settings.signal_rsi_short_min,
            "signal_short_min_score": settings.signal_short_min_score,
            "signal_short_max_score": settings.signal_short_max_score,
            "signal_require_strong": settings.signal_require_strong,
            "regime_filter_enabled": settings.regime_filter_enabled,
            "tp_multipliers": settings.tp_multipliers,
            "scale_out_fractions": settings.paper_scale_out_fractions,
            "paper_max_open_positions": settings.paper_max_open_positions,
            "paper_max_open_per_direction": settings.paper_max_open_per_direction,
            "candle_source": "desk_api",
            "note": "1h primary + BTC MTF regime via public desk API; paper caps on combined book.",
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
            {
                "symbol": r["symbol"],
                "rank": r["market_cap_rank"],
                "trades": r["trade_count"],
                "net": round(float(r["net_profit"]), 2),
            }
            for r in top_winners
        ],
        "top_losers": [
            {
                "symbol": r["symbol"],
                "rank": r["market_cap_rank"],
                "trades": r["trade_count"],
                "net": round(float(r["net_profit"]), 2),
            }
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
    print(
        json.dumps(
            {"wrote": str(out), "kpi": kpi, "independent": payload["independent"], "config": payload["config"]},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        import multiprocessing as mp

        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass
    raise SystemExit(main())
