"""7D Top-N backtest: does SIGNAL_SHORT_MIN_SCORE (floor under short band) help?

Live band today: short scores in (short_min, short_max] — default 18–25.
This sweeps short_min ∈ {0,10,12,15,18,20} with short_max=25 fixed.

    python scripts/backtest_short_min_floor.py --top 50 --days 7 --no-mtf --prefer-db
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

FLOORS = (0.0, 10.0, 12.0, 15.0, 18.0, 20.0)
SHORT_MAX = 25.0

METRIC_KEYS = (
    "trade_count",
    "win_rate",
    "net_profit",
    "profit_factor",
    "expectancy",
    "max_drawdown_percent",
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
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--capital", type=float, default=5000.0)
    parser.add_argument("--fee", type=float, default=0.05)
    parser.add_argument("--slippage", type=float, default=0.0)
    parser.add_argument("--no-mtf", action="store_true")
    parser.add_argument("--prefer-db", action="store_true")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "exports" / "short_min_floor_7d_top50.json",
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
        f"Short-min floor sweep · top {len(symbols)} · {args.days}d · "
        f"short_max={SHORT_MAX} · floors={list(FLOORS)} · "
        f"mtf={not args.no_mtf} · db={args.prefer_db}",
        file=sys.stderr,
        flush=True,
    )

    rows: list[dict] = []
    try:
        total = len(symbols) * len(FLOORS)
        step = 0
        for floor in FLOORS:
            for symbol, rank in symbols:
                step += 1
                print(
                    f"[{step}/{total}] floor>{floor:g} #{rank} {symbol}",
                    file=sys.stderr,
                    flush=True,
                )
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
                            short_max_score=SHORT_MAX,
                            short_min_score=floor,
                            require_strong_signals=False,
                        )
                    overall = _pick(report.metrics.get("overall"))
                    short = _pick(report.metrics.get("short"))
                    long = _pick(report.metrics.get("long"))
                    rows.append(
                        {
                            "short_min": floor,
                            "symbol": symbol,
                            "rank": rank,
                            "candles": report.candles_loaded,
                            "signals": report.outcome.signals_generated,
                            "overall": overall,
                            "short": short,
                            "long": long,
                        }
                    )
                    print(
                        f"  trades={int(overall['trade_count'])} "
                        f"short={int(short['trade_count'])} "
                        f"net={overall['net_profit']:.2f} "
                        f"s_net={short['net_profit']:.2f}",
                        file=sys.stderr,
                        flush=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  FAIL {exc}", file=sys.stderr, flush=True)
                    rows.append(
                        {
                            "short_min": floor,
                            "symbol": symbol,
                            "rank": rank,
                            "error": str(exc),
                        }
                    )
    finally:
        await container.aclose()

    summaries = []
    for floor in FLOORS:
        ok = [r for r in rows if r.get("short_min") == floor and "error" not in r]
        failed = sum(1 for r in rows if r.get("short_min") == floor and "error" in r)
        o_trades = sum(int(r["overall"]["trade_count"]) for r in ok)
        o_net = sum(float(r["overall"]["net_profit"]) for r in ok)
        s_trades = sum(int(r["short"]["trade_count"]) for r in ok)
        s_net = sum(float(r["short"]["net_profit"]) for r in ok)
        s_wins = 0.0
        s_losses = 0.0
        # Approximate WR from per-symbol short win_rate * trades
        wr_num = 0.0
        wr_den = 0
        for r in ok:
            n = int(r["short"]["trade_count"])
            if n <= 0:
                continue
            wr_num += float(r["short"]["win_rate"]) * n
            wr_den += n
            # pf aggregation: use sum of win/loss via expectancy if needed — keep simple
        summaries.append(
            {
                "short_min": floor,
                "label": f"short > {floor:g} … ≤ {SHORT_MAX:g}",
                "symbols_ok": len(ok),
                "symbols_failed": failed,
                "total_trades": o_trades,
                "total_net": round(o_net, 2),
                "short_trades": s_trades,
                "short_net": round(s_net, 2),
                "short_wr": round((wr_num / wr_den) * 100, 1) if wr_den else 0.0,
                "symbols_with_short": sum(
                    1 for r in ok if int(r["short"]["trade_count"]) > 0
                ),
            }
        )

    baseline = next(s for s in summaries if s["short_min"] == 18.0)
    open_floor = next(s for s in summaries if s["short_min"] == 0.0)
    for s in summaries:
        s["delta_short_net_vs_18"] = round(s["short_net"] - baseline["short_net"], 2)
        s["delta_short_trades_vs_18"] = int(s["short_trades"] - baseline["short_trades"])
        s["delta_short_net_vs_0"] = round(s["short_net"] - open_floor["short_net"], 2)

    # Marginal bucket: what do shorts only allowed when floor=0 but blocked at 18 add?
    marginal = {
        "description": "Extra short trades when lowering floor from 18 → 0 (approx via aggregate delta)",
        "extra_short_trades": int(open_floor["short_trades"] - baseline["short_trades"]),
        "extra_short_net": round(open_floor["short_net"] - baseline["short_net"], 2),
        "keep_floor_18": (open_floor["short_net"] - baseline["short_net"]) <= 0,
    }

    payload = {
        "generated_at": utc_now().isoformat(),
        "window": {"start": start.date().isoformat(), "end": end.date().isoformat(), "days": args.days},
        "universe": {"top": args.top, "symbols": [s for s, _ in symbols]},
        "gates": {
            "short_max": SHORT_MAX,
            "floors_tested": list(FLOORS),
            "timeframe": args.timeframe,
            "mtf": not args.no_mtf,
            "prefer_db": args.prefer_db,
            "fee_percent": args.fee,
            "capital": args.capital,
        },
        "summaries": summaries,
        "marginal_vs_floor_18": marginal,
        "verdict": (
            "KEEP floor 18 — opening ≤18 shorts did not improve short PnL"
            if marginal["keep_floor_18"]
            else "CONSIDER lowering floor — shorts ≤18 added positive short PnL"
        ),
        "results": rows,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("summaries", "marginal_vs_floor_18", "verdict")}, indent=2))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
