"""30-Tage A/B: vorherige Bot-Config (Hard BTC-4h-Gate) vs neu (Soft MTF-Gate).

Beide Varianten ohne Score-Blend (laut 30d Market-Blend A/B).

    python scripts/compare_soft_gate_30d.py --top 15 --days 30
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
    total_long_net = sum(float(r["long"]["net_profit"]) for r in ok)
    total_short_net = sum(float(r["short"]["net_profit"]) for r in ok)
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
        "long_net_profit": round(total_long_net, 2),
        "short_net_profit": round(total_short_net, 2),
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
    market_intelligence_enabled: bool,
    regime_soft_gate_enabled: bool,
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
            market_regime_score_enabled=False,
            regime_filter_enabled=True,
            market_intelligence_enabled=market_intelligence_enabled,
            regime_soft_gate_enabled=regime_soft_gate_enabled,
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
    market_intelligence_enabled: bool,
    regime_soft_gate_enabled: bool,
    container: Any,
) -> dict[str, Any]:
    end = utc_now()
    start = end - timedelta(days=days)
    print(
        f"\n=== {label} · intel={market_intelligence_enabled} "
        f"soft={regime_soft_gate_enabled} blend=off · {len(symbols)} symbols · "
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
                market_intelligence_enabled=market_intelligence_enabled,
                regime_soft_gate_enabled=regime_soft_gate_enabled,
            )
            rows.append(row)
            overall = row["overall"]
            print(
                f"  trades={int(overall['trade_count'])} "
                f"net={overall['net_profit']:.2f} "
                f"wr={overall['win_rate'] * 100:.1f}% "
                f"long={row['long']['net_profit']:.2f} "
                f"short={row['short']['net_profit']:.2f} "
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
        "market_intelligence_enabled": market_intelligence_enabled,
        "regime_soft_gate_enabled": regime_soft_gate_enabled,
        "aggregate": _aggregate(rows),
        "rows": rows,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--fee", type=float, default=0.05)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "exports" / "soft_gate_30d.json",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container()
    symbols = await _symbols(args.top)
    if not symbols:
        print("No symbols found in assets table.", file=sys.stderr)
        return 1

    # Previous live bot: hard BTC-4h regime (bull blocks shorts / bear blocks longs).
    old = await _run_variant(
        label="old_hard_btc4h_gate",
        symbols=symbols,
        days=args.days,
        capital=args.capital,
        fee=args.fee,
        timeframe=args.timeframe,
        market_intelligence_enabled=False,
        regime_soft_gate_enabled=False,
        container=container,
    )
    # New defaults: MarketRegimeEngine soft gate, score blend off.
    new = await _run_variant(
        label="new_soft_mtf_gate",
        symbols=symbols,
        days=args.days,
        capital=args.capital,
        fee=args.fee,
        timeframe=args.timeframe,
        market_intelligence_enabled=True,
        regime_soft_gate_enabled=True,
        container=container,
    )

    delta_net = round(
        new["aggregate"]["total_net_profit"] - old["aggregate"]["total_net_profit"],
        2,
    )
    winner = (
        "new_soft_mtf_gate"
        if delta_net > 0
        else "old_hard_btc4h_gate"
        if delta_net < 0
        else "tie"
    )
    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "days": args.days,
            "top": args.top,
            "timeframe": args.timeframe,
            "capital": args.capital,
            "fee_percent": args.fee,
            "market_regime_score_enabled": False,
            "note": (
                "old = legacy BTC-4h hard regime_from_indicators gate; "
                "new = MarketRegimeEngine soft gate (only strong bias blocks). "
                "Score blend off in both. Live F&G/funding not replayed historically."
            ),
        },
        "old_hard_btc4h_gate": {"aggregate": old["aggregate"]},
        "new_soft_mtf_gate": {"aggregate": new["aggregate"]},
        "delta_new_minus_old": {
            "total_trades": new["aggregate"]["total_trades"]
            - old["aggregate"]["total_trades"],
            "total_net_profit": delta_net,
            "long_net_profit": round(
                new["aggregate"]["long_net_profit"] - old["aggregate"]["long_net_profit"],
                2,
            ),
            "short_net_profit": round(
                new["aggregate"]["short_net_profit"] - old["aggregate"]["short_net_profit"],
                2,
            ),
            "signals_generated": new["aggregate"]["signals_generated"]
            - old["aggregate"]["signals_generated"],
            "winner_by_net_profit": winner,
        },
        "old_rows": old["rows"],
        "new_rows": new["rows"],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["old_hard_btc4h_gate"] | {"_": "old"}, indent=2))
    print(json.dumps(payload["new_soft_mtf_gate"] | {"_": "new"}, indent=2))
    print(json.dumps({"delta": payload["delta_new_minus_old"], "out": str(args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
