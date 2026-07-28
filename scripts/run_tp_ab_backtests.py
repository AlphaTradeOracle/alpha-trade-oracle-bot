"""A/B-Backtest: enge vs. grosszuegige TP-Multiples.

Baseline: 1.5R / 2.5R / 4.0R
Wide:     2.0R / 4.0R / 6.0R

Top-N Universe, letzte N Tage, gleiche Gates/Scale-out/MTF.
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

MODES: dict[str, tuple[float, float, float]] = {
    "baseline": (1.5, 2.5, 4.0),
    "wide": (2.0, 4.0, 6.0),
}


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
    parser = argparse.ArgumentParser(description="TP multiplier A/B backtests")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated symbols (skips universe top-N lookup)",
    )
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--fee", type=float, default=0.1)
    parser.add_argument("--slippage", type=float, default=0.05)
    parser.add_argument("--no-mtf", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    end = utc_now()
    start = end - timedelta(days=args.days)
    if args.symbols.strip():
        symbols = [
            (sym.strip().upper(), idx)
            for idx, sym in enumerate(args.symbols.split(","), start=1)
            if sym.strip()
        ]
    else:
        symbols = await _load_top_symbols(args.top)
    if not symbols:
        print("No universe symbols found", file=sys.stderr)
        await container.aclose()
        return 1

    print(
        f"TP A/B · {len(symbols)} symbols · {args.days}d · {args.timeframe} · "
        f"mtf={not args.no_mtf} · {start.date()} → {end.date()}",
        file=sys.stderr,
        flush=True,
    )
    for name, mults in MODES.items():
        print(f"  {name}: {mults}", file=sys.stderr, flush=True)

    results: list[dict[str, object]] = []
    try:
        total = len(symbols) * len(MODES)
        step = 0
        for mode, multipliers in MODES.items():
            for symbol, rank in symbols:
                step += 1
                print(
                    f"[{step}/{total}] {mode} #{rank} {symbol} ...",
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
                        tp_multipliers=multipliers,
                    )
                    overall = _pick(report.metrics.get("overall"))
                    results.append(
                        {
                            "mode": mode,
                            "tp_multipliers": list(multipliers),
                            "symbol": symbol,
                            "market_cap_rank": rank,
                            "candles_loaded": report.candles_loaded,
                            "signals_generated": report.outcome.signals_generated,
                            "overall": overall,
                            "long": _pick(report.metrics.get("long")),
                            "short": _pick(report.metrics.get("short")),
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
                            "mode": mode,
                            "tp_multipliers": list(multipliers),
                            "symbol": symbol,
                            "market_cap_rank": rank,
                            "error": str(exc),
                        }
                    )
    finally:
        await container.aclose()

    def _summarize(mode: str) -> dict[str, object]:
        rows = [r for r in results if r.get("mode") == mode and "error" not in r]
        failed = sum(1 for r in results if r.get("mode") == mode and "error" in r)
        total_trades = sum(int(r["overall"]["trade_count"]) for r in rows)  # type: ignore[index]
        total_net = sum(float(r["overall"]["net_profit"]) for r in rows)  # type: ignore[index]
        profitable = sum(1 for r in rows if float(r["overall"]["net_profit"]) > 0)  # type: ignore[index]
        with_trades = sum(1 for r in rows if int(r["overall"]["trade_count"]) > 0)  # type: ignore[index]
        return {
            "mode": mode,
            "tp_multipliers": list(MODES[mode]),
            "symbols_ok": len(rows),
            "symbols_failed": failed,
            "symbols_with_trades": with_trades,
            "symbols_profitable": profitable,
            "total_trades": total_trades,
            "total_net_profit": total_net,
        }

    baseline = _summarize("baseline")
    wide = _summarize("wide")
    delta_net = float(wide["total_net_profit"]) - float(baseline["total_net_profit"])
    delta_trades = int(wide["total_trades"]) - int(baseline["total_trades"])

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
        "modes": {name: list(mults) for name, mults in MODES.items()},
        "comparison": {
            "baseline": baseline,
            "wide": wide,
            "delta_net_profit": delta_net,
            "delta_trades": delta_trades,
            "wide_better": delta_net > 0,
        },
        "results": results,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
