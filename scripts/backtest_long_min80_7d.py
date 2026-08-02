"""7D universe backtest: Long score >= 80, all other live rules unchanged.

Short band stays SIGNAL_SHORT_MIN/MAX from settings (mirror still uses min_score=75).

    python scripts/backtest_long_min80_7d.py --top 100 --days 7 --prefer-db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
    "net_profit_percent",
    "profit_factor",
    "expectancy",
    "max_drawdown_percent",
    "average_win",
    "average_loss",
    "total_fees",
)


def _pick(metrics: dict | None) -> dict[str, float]:
    if not metrics:
        return {k: 0.0 for k in METRIC_KEYS}
    return {k: float(metrics.get(k, 0.0) or 0.0) for k in METRIC_KEYS}


async def _load_top(limit: int) -> list[tuple[str, int]]:
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


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=100)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--capital", type=float, default=5000.0)
    parser.add_argument("--fee", type=float, default=0.05)
    parser.add_argument("--slippage", type=float, default=0.0)
    parser.add_argument("--long-min", type=float, default=80.0)
    parser.add_argument("--no-mtf", action="store_true")
    parser.add_argument("--prefer-db", action="store_true")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "exports" / "long_min80_7d.json",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    end = utc_now()
    start = end - timedelta(days=args.days)
    symbols = await _load_top(args.top)
    if not symbols:
        print("No universe symbols", file=sys.stderr)
        await container.aclose()
        return 1

    print(
        f"Long>={args.long_min:g} · top {len(symbols)} · {args.days}d · "
        f"short_band=({settings.signal_short_min_score:g},{settings.signal_short_max_score:g}] · "
        f"min_score_mirror={settings.signal_min_score:g} · "
        f"mtf={not args.no_mtf} · db={args.prefer_db}",
        file=sys.stderr,
        flush=True,
    )

    rows: list[dict] = []
    try:
        for i, (symbol, rank) in enumerate(symbols, 1):
            print(f"[{i}/{len(symbols)}] #{rank} {symbol}", file=sys.stderr, flush=True)
            try:
                async with session_scope() as session:
                    report = await container.backtest_service.run(
                        symbol,
                        args.timeframe,
                        start,
                        end,
                        session=session if args.prefer_db else None,
                        fee_percent=args.fee,
                        slippage_percent=args.slippage,
                        initial_capital=args.capital,
                        persist=False,
                        prefer_db=args.prefer_db,
                        use_multi_timeframe=not args.no_mtf,
                        long_min_score=args.long_min,
                        # keep short gates at live settings (from_settings default)
                    )
                overall = _pick(report.metrics.get("overall"))
                long_m = _pick(report.metrics.get("long"))
                short_m = _pick(report.metrics.get("short"))
                closed = [t for t in report.outcome.trades if t.is_closed]
                long_trades = [
                    {
                        "symbol": symbol,
                        "direction": t.direction.value,
                        "entry": t.entry_price,
                        "exit": t.exit_price,
                        "net_pnl": round(t.net_pnl, 4),
                        "pnl_percent": round(t.pnl_percent, 4),
                        "exit_reason": t.exit_reason.value if t.exit_reason else None,
                        "score": round(t.signal_score, 2),
                        "holding_minutes": t.holding_minutes,
                    }
                    for t in closed
                    if t.direction.is_long
                ]
                rows.append(
                    {
                        "symbol": symbol,
                        "rank": rank,
                        "candles": report.candles_loaded,
                        "signals": report.outcome.signals_generated,
                        "overall": overall,
                        "long": long_m,
                        "short": short_m,
                        "long_trades": long_trades,
                    }
                )
                print(
                    f"  trades={int(overall['trade_count'])} "
                    f"long={int(long_m['trade_count'])} "
                    f"L_net={long_m['net_profit']:.2f} "
                    f"O_net={overall['net_profit']:.2f}",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL {exc}", file=sys.stderr, flush=True)
                rows.append({"symbol": symbol, "rank": rank, "error": str(exc)})
    finally:
        await container.aclose()

    ok = [r for r in rows if "error" not in r]
    failed = [r for r in rows if "error" in r]

    def _agg(side: str) -> dict:
        trades = sum(int(r[side]["trade_count"]) for r in ok)
        net = sum(float(r[side]["net_profit"]) for r in ok)
        fees = sum(float(r[side]["total_fees"]) for r in ok)
        # WR weighted by trade count
        wr_num = sum(float(r[side]["win_rate"]) * int(r[side]["trade_count"]) for r in ok)
        wr = (wr_num / trades) if trades else 0.0
        # PF from avg win/loss approximation is weak; sum per-symbol PF only if trades
        wins_proxy = 0.0
        loss_proxy = 0.0
        for r in ok:
            tc = int(r[side]["trade_count"])
            if tc <= 0:
                continue
            wr_s = float(r[side]["win_rate"])
            aw = float(r[side]["average_win"])
            al = float(r[side]["average_loss"])
            wins_proxy += wr_s * tc * aw
            loss_proxy += (1.0 - wr_s) * tc * al
        pf = (wins_proxy / loss_proxy) if loss_proxy > 0 else (99.0 if wins_proxy > 0 else 0.0)
        return {
            "trade_count": trades,
            "net_profit": round(net, 2),
            "net_profit_pct_vs_5k": round(net / args.capital * 100.0, 2),
            "win_rate": round(wr * 100.0, 1),
            "profit_factor": round(pf, 2),
            "fees": round(fees, 2),
            "symbols_with_trades": sum(1 for r in ok if int(r[side]["trade_count"]) > 0),
        }

    long_agg = _agg("long")
    short_agg = _agg("short")
    overall_agg = _agg("overall")

    all_long_trades: list[dict] = []
    for r in ok:
        all_long_trades.extend(r.get("long_trades") or [])
    all_long_trades.sort(key=lambda t: float(t.get("net_pnl") or 0), reverse=True)

    best = all_long_trades[:10]
    worst = list(reversed(all_long_trades[-10:])) if all_long_trades else []

    # Per-symbol long PnL leaders
    by_sym = sorted(
        [
            {
                "symbol": r["symbol"],
                "rank": r["rank"],
                "trades": int(r["long"]["trade_count"]),
                "net": float(r["long"]["net_profit"]),
                "wr": round(float(r["long"]["win_rate"]) * 100, 1),
            }
            for r in ok
            if int(r["long"]["trade_count"]) > 0
        ],
        key=lambda x: x["net"],
        reverse=True,
    )

    out = {
        "type": "long_min80_7d",
        "params": {
            "long_min_score": args.long_min,
            "min_score_mirror": settings.signal_min_score,
            "short_min": settings.signal_short_min_score,
            "short_max": settings.signal_short_max_score,
            "days": args.days,
            "top": args.top,
            "timeframe": args.timeframe,
            "capital": args.capital,
            "fee_percent": args.fee,
            "mtf": not args.no_mtf,
            "prefer_db": args.prefer_db,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "other_rules": "from_settings (ADX/RSI/RR/regime/retest/TP/BE/cooldown/…)",
        },
        "summary": {
            "symbols_ok": len(ok),
            "symbols_failed": len(failed),
            "long": long_agg,
            "short": short_agg,
            "overall": overall_agg,
        },
        "top_long_symbols": by_sym[:15],
        "bottom_long_symbols": list(reversed(by_sym[-10:])) if by_sym else [],
        "best_long_trades": best,
        "worst_long_trades": worst,
        "failed": [{"symbol": r["symbol"], "error": r["error"]} for r in failed][:20],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out["summary"], indent=2))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
