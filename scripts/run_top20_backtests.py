"""Backtest Top-N Universe-Coins ueber die letzten N Tage (Default: 20 / 28).

Nutzt die Live-Strategie-Defaults (Scale-out, MTF, Gates aus Settings).
Ausgabe: JSON auf stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta

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


async def main() -> int:
    parser = argparse.ArgumentParser(description="Top-N backtests for last N days")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--fee", type=float, default=0.1)
    parser.add_argument("--slippage", type=float, default=0.05)
    parser.add_argument(
        "--no-mtf",
        action="store_true",
        help="Single-TF statt Multi-Timeframe (schneller)",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    end = utc_now()
    start = end - timedelta(days=args.days)
    symbols = await _load_top_symbols(args.top)
    if not symbols:
        print("No universe symbols found", file=sys.stderr)
        await container.aclose()
        return 1

    print(
        f"Backtesting {len(symbols)} symbols · {args.days}d · {args.timeframe} · "
        f"mtf={not args.no_mtf} · {start.date()} → {end.date()}",
        file=sys.stderr,
        flush=True,
    )

    results: list[dict[str, object]] = []
    try:
        for idx, (symbol, rank) in enumerate(symbols, start=1):
            print(
                f"[{idx}/{len(symbols)}] #{rank} {symbol} ...",
                file=sys.stderr,
                flush=True,
            )
            try:
                report = await container.backtest_service.run(
                    symbol,
                    args.timeframe,
                    start,
                    end,
                    fee_percent=args.fee,
                    slippage_percent=args.slippage,
                    initial_capital=args.capital,
                    persist=False,
                    use_multi_timeframe=not args.no_mtf,
                )
                overall = _pick(report.metrics.get("overall"))
                long_m = _pick(report.metrics.get("long"))
                short_m = _pick(report.metrics.get("short"))
                results.append(
                    {
                        "symbol": symbol,
                        "market_cap_rank": rank,
                        "timeframe": args.timeframe,
                        "start": start.date().isoformat(),
                        "end": end.date().isoformat(),
                        "candles_loaded": report.candles_loaded,
                        "signals_generated": report.outcome.signals_generated,
                        "overall": overall,
                        "long": long_m,
                        "short": short_m,
                    }
                )
                print(
                    f"  trades={int(overall['trade_count'])} "
                    f"net={overall['net_profit']:.2f} "
                    f"wr={overall['win_rate'] * 100:.1f}% "
                    f"pf={overall['profit_factor']:.2f}",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception as exc:
                print(f"  FAILED: {exc}", file=sys.stderr, flush=True)
                results.append(
                    {
                        "symbol": symbol,
                        "market_cap_rank": rank,
                        "timeframe": args.timeframe,
                        "error": str(exc),
                    }
                )
    finally:
        await container.aclose()

    ok = [r for r in results if "error" not in r]
    total_trades = sum(int(r["overall"]["trade_count"]) for r in ok)  # type: ignore[index]
    total_net = sum(float(r["overall"]["net_profit"]) for r in ok)  # type: ignore[index]
    winners = sum(1 for r in ok if float(r["overall"]["net_profit"]) > 0)  # type: ignore[index]

    payload = {
        "generated_at": utc_now().isoformat(),
        "top_n": args.top,
        "days": args.days,
        "timeframe": args.timeframe,
        "use_multi_timeframe": not args.no_mtf,
        "capital": args.capital,
        "fee_percent": args.fee,
        "slippage_percent": args.slippage,
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "gates": {
            "min_score": settings.signal_min_score,
            "short_max_score": settings.signal_short_max_score,
            "require_strong": settings.signal_require_strong,
            "min_rr": settings.min_risk_reward_ratio,
            "scale_out_enabled": True,
        },
        "summary": {
            "symbols_ok": len(ok),
            "symbols_failed": len(results) - len(ok),
            "total_trades": total_trades,
            "total_net_profit": total_net,
            "symbols_profitable": winners,
        },
        "results": results,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
