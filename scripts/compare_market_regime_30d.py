"""30-Tage A/B: Coin-only vs Market-Regime-blend (DB candles).

Vergleicht die bestehende Signal-Logik ohne Market-Score-Blend mit der neuen
gewichteten Coin+BTC-Market-Bewertung auf demselben Universum.

    python scripts/compare_market_regime_30d.py --top 20 --days 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.container import build_container  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.core.time import utc_now  # noqa: E402
from app.database.session import session_scope  # noqa: E402
from app.models.market import Asset  # noqa: E402

METRIC_KEYS = (
    "trade_count",
    "win_rate",
    "net_profit",
    "profit_factor",
    "expectancy",
    "max_drawdown",
    "max_drawdown_percent",
)


def _pick(metrics: dict[str, float] | None) -> dict[str, float]:
    if not metrics:
        return {key: 0.0 for key in METRIC_KEYS}
    return {key: float(metrics.get(key, 0.0) or 0.0) for key in METRIC_KEYS}


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if "error" not in r]
    failed = [r for r in rows if "error" in r]
    with_trades = [r for r in ok if int(r["overall"]["trade_count"]) > 0]
    total_trades = sum(int(r["overall"]["trade_count"]) for r in ok)
    total_net = sum(float(r["overall"]["net_profit"]) for r in ok)
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
    return {
        "symbols_ok": len(ok),
        "symbols_failed": len(failed),
        "symbols_with_trades": len(with_trades),
        "total_trades": total_trades,
        "total_net_profit": round(total_net, 2),
        "avg_win_rate": round(wr_num / total_trades, 4) if total_trades else 0.0,
        "avg_profit_factor": round(pf_num / pf_den, 4) if pf_den else 0.0,
        "signals_generated": sum(int(r.get("signals_generated", 0)) for r in ok),
    }


async def _symbols(limit: int) -> list[tuple[str, int]]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Asset.symbol, Asset.market_cap_rank)
                .where(
                    Asset.is_active.is_(True),
                    Asset.market_cap_rank.is_not(None),
                    Asset.symbol.notin_(["BTCUSDT", "ETHUSDT"]),
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
    market_enabled: bool,
) -> dict[str, Any]:
    async with session_scope() as session:
        report = await container.backtest_service.run(
            symbol,
            timeframe,
            start,
            end,
            session=session,
            fee_percent=fee,
            slippage_percent=0.05,
            initial_capital=capital,
            persist=False,
            prefer_db=True,
            use_multi_timeframe=True,
            market_regime_score_enabled=market_enabled,
            regime_filter_enabled=True,
        )
    return {
        "symbol": symbol,
        "market_cap_rank": rank,
        "candles_loaded": report.candles_loaded,
        "signals_generated": report.outcome.signals_generated,
        "overall": _pick(report.metrics.get("overall")),
        "long": _pick(report.metrics.get("long")),
        "short": _pick(report.metrics.get("short")),
    }


async def _run_variant(
    *,
    label: str,
    symbols: list[tuple[str, int]],
    days: int,
    capital: float,
    fee: float,
    timeframe: str,
    market_enabled: bool,
    container: Any,
) -> dict[str, Any]:
    end = utc_now()
    start = end - timedelta(days=days)
    print(
        f"\n=== {label} · market_blend={market_enabled} · {len(symbols)} symbols · "
        f"{days}d · {start.date()} → {end.date()} ===",
        file=sys.stderr,
        flush=True,
    )
    rows: list[dict[str, Any]] = []
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
                market_enabled=market_enabled,
            )
            rows.append(row)
            overall = row["overall"]
            print(
                f"  trades={int(overall['trade_count'])} "
                f"net={overall['net_profit']:.2f} "
                f"wr={overall['win_rate'] * 100:.1f}% "
                f"sig={row['signals_generated']}",
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}", file=sys.stderr, flush=True)
            rows.append(
                {
                    "symbol": symbol,
                    "market_cap_rank": rank,
                    "error": str(exc),
                }
            )
    return {
        "label": label,
        "market_regime_score_enabled": market_enabled,
        "aggregate": _aggregate(rows),
        "rows": rows,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--fee", type=float, default=0.05)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "exports" / "market_regime_30d.json",
    )
    args = parser.parse_args()

    configure_logging(get_settings())
    container = build_container()
    symbols = await _symbols(args.top)
    if not symbols:
        print("No symbols found in assets table.", file=sys.stderr)
        return 1

    baseline = await _run_variant(
        label="coin_only",
        symbols=symbols,
        days=args.days,
        capital=args.capital,
        fee=args.fee,
        timeframe=args.timeframe,
        market_enabled=False,
        container=container,
    )
    market = await _run_variant(
        label="market_blend",
        symbols=symbols,
        days=args.days,
        capital=args.capital,
        fee=args.fee,
        timeframe=args.timeframe,
        market_enabled=True,
        container=container,
    )

    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "days": args.days,
            "top": args.top,
            "timeframe": args.timeframe,
            "capital": args.capital,
            "fee_percent": args.fee,
            "weights": {
                "coin": 0.60,
                "market": 0.25,
                "funding": 0.05,
                "open_interest": 0.05,
                "liquidations": 0.05,
            },
            "note": (
                "Funding/OI/Liquidations/Dominance/Fear&Greed are stubs "
                "(weight redistributed). BTC multi-TF + ETH drive the market score."
            ),
        },
        "coin_only": {"aggregate": baseline["aggregate"]},
        "market_blend": {"aggregate": market["aggregate"]},
        "delta": {
            "total_trades": market["aggregate"]["total_trades"]
            - baseline["aggregate"]["total_trades"],
            "total_net_profit": round(
                market["aggregate"]["total_net_profit"]
                - baseline["aggregate"]["total_net_profit"],
                2,
            ),
            "signals_generated": market["aggregate"]["signals_generated"]
            - baseline["aggregate"]["signals_generated"],
        },
        "coin_only_rows": baseline["rows"],
        "market_blend_rows": market["rows"],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["coin_only"] | {"_": "coin_only"}, indent=2))
    print(json.dumps(payload["market_blend"] | {"_": "market_blend"}, indent=2))
    print(json.dumps({"delta": payload["delta"], "out": str(args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
