"""Deep-Dive: aktuelle Live-Strategie gegen Top-N Kerzen aus der DB.

Laedt OHLCV aus ``market_candles`` (kein Exchange-API). Zwei Laeufe:
  1) live-parity: primary 1h + MTF (kurze DB-Historie)
  2) long-horizon: primary 1d single-TF (~1 Jahr DB-Tiefe)

Ausgabe: JSON (stdout + optional --out Datei) mit Aggregaten, Walk-Forward,
Long/Short, Rank-Buckets.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.container import build_container
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.time import utc_now
from app.database.session import session_scope
from app.models.market import Asset

METRIC_KEYS = (
    "trade_count",
    "win_rate",
    "net_profit",
    "profit_factor",
    "expectancy",
    "max_drawdown",
    "max_drawdown_percent",
    "average_win",
    "average_loss",
    "average_holding_minutes",
    "total_fees",
)


def _pick(metrics: dict[str, float] | None) -> dict[str, float]:
    if not metrics:
        return {key: 0.0 for key in METRIC_KEYS}
    return {key: float(metrics.get(key, 0.0) or 0.0) for key in METRIC_KEYS}


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def _rank_bucket(rank: int) -> str:
    if rank <= 50:
        return "1-50"
    if rank <= 100:
        return "51-100"
    if rank <= 200:
        return "101-200"
    if rank <= 300:
        return "201-300"
    return "301+"


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if "error" not in r]
    failed = [r for r in rows if "error" in r]
    total_trades = sum(int(r["overall"]["trade_count"]) for r in ok)
    total_net = sum(float(r["overall"]["net_profit"]) for r in ok)
    total_fees = sum(float(r["overall"]["total_fees"]) for r in ok)
    profitable = sum(1 for r in ok if float(r["overall"]["net_profit"]) > 0)
    with_trades = [r for r in ok if int(r["overall"]["trade_count"]) > 0]

    wr_num = sum(
        float(r["overall"]["win_rate"]) * int(r["overall"]["trade_count"]) for r in with_trades
    )
    pf_num = sum(
        float(r["overall"]["profit_factor"]) * int(r["overall"]["trade_count"])
        for r in with_trades
        if math.isfinite(float(r["overall"]["profit_factor"]))
    )
    pf_den = sum(
        int(r["overall"]["trade_count"])
        for r in with_trades
        if math.isfinite(float(r["overall"]["profit_factor"]))
    )
    dd_worst = max(
        (float(r["overall"]["max_drawdown_percent"]) for r in with_trades),
        default=0.0,
    )
    expect_avg = _safe_div(
        sum(float(r["overall"]["expectancy"]) for r in with_trades),
        len(with_trades),
    )

    long_trades = sum(int(r["long"]["trade_count"]) for r in ok)
    short_trades = sum(int(r["short"]["trade_count"]) for r in ok)
    long_net = sum(float(r["long"]["net_profit"]) for r in ok)
    short_net = sum(float(r["short"]["net_profit"]) for r in ok)

    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"symbols": 0, "trades": 0, "net": 0.0, "profitable": 0}
    )
    for r in ok:
        b = _rank_bucket(int(r["market_cap_rank"]))
        buckets[b]["symbols"] += 1
        buckets[b]["trades"] += int(r["overall"]["trade_count"])
        buckets[b]["net"] += float(r["overall"]["net_profit"])
        if float(r["overall"]["net_profit"]) > 0:
            buckets[b]["profitable"] += 1

    top_winners = sorted(
        with_trades, key=lambda r: float(r["overall"]["net_profit"]), reverse=True
    )[:15]
    top_losers = sorted(with_trades, key=lambda r: float(r["overall"]["net_profit"]))[:15]

    return {
        "symbols_ok": len(ok),
        "symbols_failed": len(failed),
        "symbols_with_trades": len(with_trades),
        "total_trades": total_trades,
        "total_net_profit": round(total_net, 2),
        "total_fees": round(total_fees, 2),
        "symbols_profitable": profitable,
        "symbol_hit_rate": round(_safe_div(profitable, len(with_trades)), 4),
        "trade_weighted_win_rate": round(_safe_div(wr_num, total_trades), 4),
        "trade_weighted_profit_factor": round(_safe_div(pf_num, pf_den), 4),
        "avg_expectancy_per_symbol": round(expect_avg, 4),
        "worst_symbol_max_dd_pct": round(dd_worst, 4),
        "long": {
            "trades": long_trades,
            "net_profit": round(long_net, 2),
            "win_rate": round(
                _safe_div(
                    sum(float(r["long"]["win_rate"]) * int(r["long"]["trade_count"]) for r in ok),
                    long_trades,
                ),
                4,
            ),
        },
        "short": {
            "trades": short_trades,
            "net_profit": round(short_net, 2),
            "win_rate": round(
                _safe_div(
                    sum(float(r["short"]["win_rate"]) * int(r["short"]["trade_count"]) for r in ok),
                    short_trades,
                ),
                4,
            ),
        },
        "rank_buckets": {
            k: {
                "symbols": int(v["symbols"]),
                "trades": int(v["trades"]),
                "net_profit": round(v["net"], 2),
                "profitable_symbols": int(v["profitable"]),
            }
            for k, v in sorted(buckets.items())
        },
        "top_winners": [
            {
                "symbol": r["symbol"],
                "rank": r["market_cap_rank"],
                "trades": int(r["overall"]["trade_count"]),
                "net": round(float(r["overall"]["net_profit"]), 2),
                "wr": round(float(r["overall"]["win_rate"]), 3),
            }
            for r in top_winners
        ],
        "top_losers": [
            {
                "symbol": r["symbol"],
                "rank": r["market_cap_rank"],
                "trades": int(r["overall"]["trade_count"]),
                "net": round(float(r["overall"]["net_profit"]), 2),
                "wr": round(float(r["overall"]["win_rate"]), 3),
            }
            for r in top_losers
        ],
        "failed_symbols": [
            {"symbol": r["symbol"], "rank": r["market_cap_rank"], "error": r["error"]}
            for r in failed[:40]
        ],
    }


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


async def _backtest_one(
    container: Any,
    *,
    symbol: str,
    rank: int,
    timeframe: str,
    start: Any,
    end: Any,
    capital: float,
    fee: float,
    slippage: float,
    use_mtf: bool,
) -> dict[str, Any]:
    async with session_scope() as session:
        report = await container.backtest_service.run(
            symbol,
            timeframe,
            start,
            end,
            session=session,
            fee_percent=fee,
            slippage_percent=slippage,
            initial_capital=capital,
            persist=False,
            prefer_db=True,
            use_multi_timeframe=use_mtf,
        )
    return {
        "symbol": symbol,
        "market_cap_rank": rank,
        "timeframe": timeframe,
        "candles_loaded": report.candles_loaded,
        "signals_generated": report.outcome.signals_generated,
        "overall": _pick(report.metrics.get("overall")),
        "long": _pick(report.metrics.get("long")),
        "short": _pick(report.metrics.get("short")),
    }


async def _run_batch(
    *,
    label: str,
    symbols: list[tuple[str, int]],
    timeframe: str,
    days: int,
    capital: float,
    fee: float,
    slippage: float,
    use_mtf: bool,
    container: Any,
    walk_forward: bool,
) -> dict[str, Any]:
    end = utc_now()
    start = end - timedelta(days=days)
    print(
        f"\n=== {label} · {len(symbols)} symbols · {days}d · {timeframe} · "
        f"mtf={use_mtf} · DB · {start.date()} → {end.date()} ===",
        file=sys.stderr,
        flush=True,
    )

    results: list[dict[str, Any]] = []
    for idx, (symbol, rank) in enumerate(symbols, start=1):
        print(f"[{idx}/{len(symbols)}] #{rank} {symbol} ...", file=sys.stderr, flush=True)
        try:
            row = await _backtest_one(
                container,
                symbol=symbol,
                rank=rank,
                timeframe=timeframe,
                start=start,
                end=end,
                capital=capital,
                fee=fee,
                slippage=slippage,
                use_mtf=use_mtf,
            )
            results.append(row)
            overall = row["overall"]
            print(
                f"  trades={int(overall['trade_count'])} "
                f"net={overall['net_profit']:.2f} "
                f"wr={overall['win_rate'] * 100:.1f}% "
                f"pf={overall['profit_factor']:.2f} "
                f"sig={row['signals_generated']}",
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:
            print(f"  FAILED: {exc}", file=sys.stderr, flush=True)
            results.append(
                {
                    "symbol": symbol,
                    "market_cap_rank": rank,
                    "timeframe": timeframe,
                    "error": str(exc),
                }
            )

    walk_forward_payload: dict[str, Any] | None = None
    if walk_forward:
        mid = start + (end - start) / 2
        halves: dict[str, list[dict[str, Any]]] = {}
        # Nur Symbole mit genug Primary-Kerzen im Full-Lauf
        subset = [r for r in results if "error" not in r and int(r.get("candles_loaded", 0)) > 0]
        for half_name, half_start, half_end in (
            ("first_half", start, mid),
            ("second_half", mid, end),
        ):
            print(
                f"--- walk-forward {half_name} ({len(subset)} symbols) ---",
                file=sys.stderr,
                flush=True,
            )
            half_rows: list[dict[str, Any]] = []
            for idx, base in enumerate(subset, start=1):
                symbol = str(base["symbol"])
                rank = int(base["market_cap_rank"])
                if idx == 1 or idx % 25 == 0 or idx == len(subset):
                    print(
                        f"  wf [{idx}/{len(subset)}] {symbol}",
                        file=sys.stderr,
                        flush=True,
                    )
                try:
                    half_rows.append(
                        await _backtest_one(
                            container,
                            symbol=symbol,
                            rank=rank,
                            timeframe=timeframe,
                            start=half_start,
                            end=half_end,
                            capital=capital,
                            fee=fee,
                            slippage=slippage,
                            use_mtf=use_mtf,
                        )
                    )
                except Exception as exc:
                    half_rows.append(
                        {
                            "symbol": symbol,
                            "market_cap_rank": rank,
                            "error": str(exc),
                        }
                    )
            halves[half_name] = half_rows

        walk_forward_payload = {
            "first_half": {
                "start": start.date().isoformat(),
                "end": mid.date().isoformat(),
                "summary": _aggregate(halves["first_half"]),
            },
            "second_half": {
                "start": mid.date().isoformat(),
                "end": end.date().isoformat(),
                "summary": _aggregate(halves["second_half"]),
            },
        }

    return {
        "label": label,
        "timeframe": timeframe,
        "days": days,
        "use_multi_timeframe": use_mtf,
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "capital": capital,
        "fee_percent": fee,
        "slippage_percent": slippage,
        "candle_source": "db",
        "summary": _aggregate(results),
        "walk_forward": walk_forward_payload,
        "results": results,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Top-N DB backtests deep dive")
    parser.add_argument("--top", type=int, default=300)
    parser.add_argument("--capital", type=float, default=5_000.0)
    parser.add_argument("--fee", type=float, default=0.1)
    parser.add_argument("--slippage", type=float, default=0.05)
    parser.add_argument("--live-days", type=int, default=22, help="1h MTF Fenster")
    parser.add_argument("--long-days", type=int, default=500, help="1d Fenster")
    parser.add_argument("--skip-live", action="store_true")
    parser.add_argument("--skip-long", action="store_true")
    parser.add_argument("--walk-forward", action="store_true", help="Halbiertes Fenster je Lauf")
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    symbols = await _load_top_symbols(args.top)
    if not symbols:
        print("No universe symbols found", file=sys.stderr)
        await container.aclose()
        return 1

    runs: list[dict[str, Any]] = []
    try:
        if not args.skip_live:
            runs.append(
                await _run_batch(
                    label="live_parity_1h_mtf",
                    symbols=symbols,
                    timeframe="1h",
                    days=args.live_days,
                    capital=args.capital,
                    fee=args.fee,
                    slippage=args.slippage,
                    use_mtf=True,
                    container=container,
                    walk_forward=args.walk_forward,
                )
            )
        if not args.skip_long:
            runs.append(
                await _run_batch(
                    label="long_horizon_1d",
                    symbols=symbols,
                    timeframe="1d",
                    days=args.long_days,
                    capital=args.capital,
                    fee=args.fee,
                    slippage=args.slippage,
                    use_mtf=False,
                    container=container,
                    walk_forward=args.walk_forward,
                )
            )
    finally:
        await container.aclose()

    payload = {
        "generated_at": utc_now().isoformat(),
        "top_n": args.top,
        "strategy_gates": {
            "min_score": settings.signal_min_score,
            "short_max_score": settings.signal_short_max_score,
            "require_strong": settings.signal_require_strong,
            "min_rr": settings.min_risk_reward_ratio,
            "min_adx": settings.signal_min_adx,
            "rsi_long_max": settings.signal_rsi_long_max,
            "rsi_short_min": settings.signal_rsi_short_min,
            "block_range": settings.signal_block_range_market,
            "cooldown_minutes": settings.signal_cooldown_minutes,
            "tp_multipliers": [1.5, 2.5, 4.0],
            "scale_out": True,
            "be_after_tp1": True,
        },
        "notes": [
            "Kerzen ausschliesslich aus market_candles (DB).",
            "1h-Historie in DB aktuell ~3 Wochen → live_parity ist kurzes Fenster.",
            "1d-Historie ~500 Tage → long_horizon deckt ~1 Jahr ab (single-TF).",
            "Pro Symbol eigenes Kapital (kein shared Portfolio).",
            "Sizing via RiskManager/reference capital — nicht Paper $100-Margin.",
        ],
        "runs": runs,
    }

    text = json.dumps(payload, indent=2)
    if args.out:
        path = Path(args.out)
        path.write_text(text, encoding="utf-8")
        print(f"Wrote {path}", file=sys.stderr, flush=True)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
