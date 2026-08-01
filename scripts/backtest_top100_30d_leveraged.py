#!/usr/bin/env python3
"""Alte Strategie · Top-100 Crypto · 30 Tage · 10× Hebel · Start 5000.

Hinweis: Im Repo liegen nur Crypto-OHLCV (KuCoin/Binance), keine klassischen
Aktien. „Top 100“ = Universe nach Market-Cap-Rank.

  .venv/bin/python scripts/backtest_top100_30d_leveraged.py \\
      --top 100 --days 30 --leverage 10 --capital 5000 \\
      --out exports/top100_30d_lev10.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.backtesting.engine import WARMUP_CANDLES, BacktestConfig, BacktestEngine
from app.backtesting.metrics import compute_metrics
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.time import timeframe_to_timedelta, utc_now
from app.database.session import session_scope
from app.models.market import Asset
from app.repositories.asset_repository import AssetRepository
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

METRIC_KEYS = (
    "trade_count",
    "win_rate",
    "net_profit",
    "profit_factor",
    "expectancy",
    "max_drawdown_percent",
    "total_fees",
)


@dataclass
class FlatTrade:
    symbol: str
    direction: str
    entry_at: str
    exit_at: str
    entry_price: float
    exit_price: float
    stop_loss: float
    quantity: float
    net_pnl: float
    fees: float
    exit_reason: str | None
    signal_score: float
    rank: int


def _pick(metrics: dict[str, float] | None) -> dict[str, float]:
    if not metrics:
        return {key: 0.0 for key in METRIC_KEYS}
    return {key: float(metrics.get(key, 0.0) or 0.0) for key in METRIC_KEYS}


def _base_kwargs(settings, *, leverage: float, capital: float) -> dict[str, Any]:
    return {
        "timeframe": settings.primary_timeframe,
        "fee_percent": float(settings.paper_fee_percent),
        "slippage_percent": 0.05,
        "initial_capital": capital,
        "min_score": settings.signal_min_score,
        "min_risk_reward_ratio": settings.min_risk_reward_ratio,
        "atr_multiplier": settings.atr_multiplier,
        "max_atr_percent": settings.max_atr_percent,
        "expiry_multiplier": settings.signal_expiry_multiplier,
        "timeframes": list(settings.timeframes),
        "use_multi_timeframe": False,
        "cooldown_minutes": settings.signal_cooldown_minutes,
        "require_strong_signals": settings.signal_require_strong,
        "block_range_market": settings.signal_block_range_market,
        "min_adx": settings.signal_min_adx,
        "rsi_long_max": settings.signal_rsi_long_max,
        "rsi_short_min": settings.signal_rsi_short_min,
        "scale_out_enabled": True,
        "scale_out_fractions": list(settings.parsed_scale_out_fractions),
        "move_stop_to_breakeven_after_tp1": settings.paper_move_stop_to_breakeven,
        "tp_multipliers": list(settings.parsed_tp_multipliers),
        "retest_entry_enabled": settings.backtest_retest_entry_enabled,
        "retest_zone_near": settings.paper_retest_zone_near,
        "retest_zone_far": settings.paper_retest_zone_far,
        "retest_pending_multiplier": settings.paper_retest_pending_multiplier,
        "weights": DEFAULT_WEIGHTS.without_sentiment().model_dump(),
        "lesson_skip_rules": [],
        "leverage": leverage,
        "risk_per_trade_usd": float(settings.paper_risk_per_trade_usd),
        "max_notional_usd": float(settings.paper_max_notional_usd),
    }


def _run_symbol(payload: dict[str, Any]) -> dict[str, Any]:
    logging.disable(logging.INFO)
    symbol = payload["symbol"]
    rank = payload["rank"]
    base = dict(payload["base_kwargs"])
    weights = base.pop("weights")
    if isinstance(weights, dict):
        base["weights"] = StrategyWeights(**weights)
    for key in (
        "timeframes",
        "scale_out_fractions",
        "tp_multipliers",
        "lesson_skip_rules",
    ):
        if isinstance(base.get(key), list):
            base[key] = tuple(base[key])

    df = pd.DataFrame(payload["frame"])
    if "open_time" in df.columns:
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        df = df.set_index("open_time", drop=False)

    try:
        config = BacktestConfig(symbol=symbol, **base)
        outcome = BacktestEngine(config).run(df)
        metrics = compute_metrics(outcome)
        trades = []
        for t in outcome.trades:
            if t.exit_at is None or t.exit_price is None:
                continue
            trades.append(
                asdict(
                    FlatTrade(
                        symbol=symbol,
                        direction=t.direction.value,
                        entry_at=t.entry_at.isoformat(),
                        exit_at=t.exit_at.isoformat(),
                        entry_price=float(t.entry_price),
                        exit_price=float(t.exit_price),
                        stop_loss=float(t.stop_loss),
                        quantity=float(t.quantity),
                        net_pnl=float(t.net_pnl),
                        fees=float(t.fees),
                        exit_reason=t.exit_reason.value if t.exit_reason else None,
                        signal_score=float(t.signal_score),
                        rank=rank,
                    )
                )
            )
        return {
            "symbol": symbol,
            "rank": rank,
            "overall": _pick(metrics.get("overall")),
            "long": _pick(metrics.get("long")),
            "short": _pick(metrics.get("short")),
            "trades": trades,
            "signals_generated": int(outcome.signals_generated),
        }
    except Exception as exc:  # noqa: BLE001
        return {"symbol": symbol, "rank": rank, "error": str(exc), "trades": []}


def _portfolio_replay(
    trades: list[dict[str, Any]],
    *,
    capital: float,
    leverage: float,
    fee_percent: float,
    max_open: int,
    max_per_direction: int,
    max_portfolio_risk_pct: float,
    risk_per_trade: float,
) -> dict[str, Any]:
    """Chronologischer Replay auf einem gemeinsamen 5k-Konto mit Margin/Hebel."""
    events: list[tuple[datetime, str, dict[str, Any]]] = []
    for t in trades:
        entry = datetime.fromisoformat(t["entry_at"])
        exit_ = datetime.fromisoformat(t["exit_at"])
        events.append((entry, "open", t))
        events.append((exit_, "close", t))
    events.sort(key=lambda x: (x[0], 0 if x[1] == "close" else 1))

    cash = capital
    open_pos: dict[str, dict[str, Any]] = {}
    equity_curve = [{"t": None, "equity": capital}]
    realized = 0.0
    skipped = {"no_cash": 0, "max_open": 0, "max_dir": 0, "portfolio_risk": 0, "busy": 0}
    taken = 0
    wins = 0
    gross_wins = 0.0
    gross_losses = 0.0
    peak = capital
    max_dd = 0.0
    trade_pnls: list[float] = []

    def _equity() -> float:
        # Mark-to-entry approximation while open (no live marks in this replay)
        locked = sum(float(p["margin"]) for p in open_pos.values())
        return cash + locked

    def _open_risk() -> float:
        return sum(float(p["risk"]) for p in open_pos.values())

    def _dir_count(direction: str) -> int:
        return sum(1 for p in open_pos.values() if p["direction"] == direction)

    for ts, kind, t in events:
        key = f"{t['symbol']}|{t['entry_at']}"
        if kind == "close":
            pos = open_pos.pop(key, None)
            if pos is None:
                continue
            # Use engine net_pnl (already fee-adjusted) for the sized quantity
            pnl = float(t["net_pnl"])
            cash += float(pos["margin"]) + pnl
            realized += pnl
            trade_pnls.append(pnl)
            taken += 1
            if pnl > 0:
                wins += 1
                gross_wins += pnl
            else:
                gross_losses += abs(pnl)
            eq = _equity()
            peak = max(peak, eq)
            max_dd = max(max_dd, peak - eq)
            equity_curve.append({"t": ts.isoformat(), "equity": round(eq, 2)})
            continue

        # open
        if key in open_pos or t["symbol"] in {p["symbol"] for p in open_pos.values()}:
            skipped["busy"] += 1
            continue
        if len(open_pos) >= max_open:
            skipped["max_open"] += 1
            continue
        direction = t["direction"]
        is_long = "LONG" in direction
        if _dir_count(direction) >= max_per_direction:
            # also count STRONG_* as same side
            side = "LONG" if is_long else "SHORT"
            if sum(1 for p in open_pos.values() if (("LONG" in p["direction"]) == is_long)) >= max_per_direction:
                skipped["max_dir"] += 1
                continue
        eq = _equity()
        if max_portfolio_risk_pct > 0:
            if (_open_risk() + risk_per_trade) > eq * (max_portfolio_risk_pct / 100.0):
                skipped["portfolio_risk"] += 1
                continue

        notional = float(t["quantity"]) * float(t["entry_price"])
        margin = notional / max(leverage, 1e-9)
        # Fees are already inside engine ``net_pnl`` — only lock margin here.
        if cash < margin:
            skipped["no_cash"] += 1
            continue

        cash -= margin
        open_pos[key] = {
            "symbol": t["symbol"],
            "direction": direction,
            "margin": margin,
            "risk": risk_per_trade,
        }
        equity_curve.append({"t": ts.isoformat(), "equity": round(_equity(), 2)})

    # Force-close leftovers at last known exit pnl
    for key, pos in list(open_pos.items()):
        # should be rare if events are complete
        cash += float(pos["margin"])
        open_pos.pop(key, None)

    final_equity = cash
    pf = (gross_wins / gross_losses) if gross_losses > 0 else (99.0 if gross_wins > 0 else 0.0)
    return {
        "start_capital": capital,
        "final_equity": round(final_equity, 2),
        "net_profit": round(final_equity - capital, 2),
        "return_pct": round((final_equity / capital - 1.0) * 100.0, 2),
        "trades_taken": taken,
        "win_rate": round(wins / taken, 4) if taken else 0.0,
        "profit_factor": round(pf, 4),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round((max_dd / peak) * 100.0, 2) if peak else 0.0,
        "skipped": skipped,
        "equity_curve_points": len(equity_curve),
        "avg_trade": round(sum(trade_pnls) / taken, 2) if taken else 0.0,
    }


def _aggregate_symbol_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
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
        if math.isfinite(float(r["overall"]["profit_factor"])) and float(r["overall"]["profit_factor"]) > 0
    )
    pf_den = sum(
        int(r["overall"]["trade_count"])
        for r in with_trades
        if math.isfinite(float(r["overall"]["profit_factor"])) and float(r["overall"]["profit_factor"]) > 0
    )
    return {
        "symbols_ok": len(ok),
        "symbols_failed": len(failed),
        "symbols_with_trades": len(with_trades),
        "total_trades": total_trades,
        "total_net_profit": round(total_net, 2),
        "avg_win_rate": round(wr_num / total_trades, 4) if total_trades else 0.0,
        "avg_profit_factor": round(pf_num / pf_den, 4) if pf_den else 0.0,
        "note": "Sum of per-symbol books each starting at --capital (not shared portfolio)",
    }


async def _load_symbols(limit: int) -> list[tuple[str, int]]:
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


async def _load_frames(
    symbols: list[tuple[str, int]], timeframe: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = []
    async with session_scope() as session:
        repo = AssetRepository(session)
        warmup_start = start - timeframe_to_timedelta(timeframe) * WARMUP_CANDLES
        for idx, (symbol, rank) in enumerate(symbols, start=1):
            series = await repo.load_candle_series(
                symbol,
                timeframe,
                start_time=warmup_start,
                end_time=end,
                limit=100_000,
            )
            if series.is_empty:
                loaded.append({"symbol": symbol, "rank": rank, "error": "no candles"})
                continue
            df = series.to_dataframe().reset_index(drop=False)
            if len(df) < WARMUP_CANDLES + 48:
                loaded.append(
                    {
                        "symbol": symbol,
                        "rank": rank,
                        "error": f"insufficient bars: {len(df)}",
                    }
                )
                continue
            loaded.append(
                {
                    "symbol": symbol,
                    "rank": rank,
                    "frame": df.to_dict(orient="list"),
                    "bars": len(df),
                }
            )
            if idx % 25 == 0 or idx == len(symbols):
                print(f"loaded {idx}/{len(symbols)}", file=sys.stderr, flush=True)
    return loaded


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=100)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--leverage", type=float, default=10.0)
    parser.add_argument("--capital", type=float, default=5000.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", type=Path, default=Path("exports/top100_30d_lev10.json"))
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    logging.getLogger("app").setLevel(logging.ERROR)

    end = utc_now().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=args.days)
    print(
        f"OLD strategy | top {args.top} crypto | {args.days}d | "
        f"lev {args.leverage}x | start {args.capital} | {start.date()} → {end.date()}",
        file=sys.stderr,
        flush=True,
    )
    print(
        "NOTE: No traditional stock data in DB — using crypto universe by market-cap rank.",
        file=sys.stderr,
        flush=True,
    )

    symbols = await _load_symbols(args.top)
    frames = await _load_frames(symbols, settings.primary_timeframe, start, end)
    usable = [f for f in frames if "frame" in f]
    skipped = [f for f in frames if "error" in f]
    print(f"candles: {len(usable)} ok, {len(skipped)} skipped", file=sys.stderr, flush=True)

    base = _base_kwargs(settings, leverage=args.leverage, capital=args.capital)
    jobs = [
        {
            "symbol": item["symbol"],
            "rank": item["rank"],
            "frame": item["frame"],
            "base_kwargs": dict(base),
        }
        for item in usable
    ]

    rows: list[dict[str, Any]] = [
        {"symbol": s["symbol"], "rank": s["rank"], "error": s["error"], "trades": []}
        for s in skipped
    ]
    all_trades: list[dict[str, Any]] = []
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = [pool.submit(_run_symbol, job) for job in jobs]
        for fut in as_completed(futs):
            row = fut.result()
            rows.append(row)
            all_trades.extend(row.get("trades") or [])
            done += 1
            if done % 10 == 0 or done == len(futs):
                print(
                    f"backtested {done}/{len(futs)} ({time.time() - t0:.0f}s)",
                    file=sys.stderr,
                    flush=True,
                )

    per_symbol = _aggregate_symbol_rows(rows)
    portfolio = _portfolio_replay(
        all_trades,
        capital=args.capital,
        leverage=args.leverage,
        fee_percent=float(settings.paper_fee_percent),
        max_open=int(settings.paper_max_open_positions),
        max_per_direction=int(settings.paper_max_open_per_direction),
        max_portfolio_risk_pct=float(settings.paper_max_portfolio_risk_pct),
        risk_per_trade=float(settings.paper_risk_per_trade_usd),
    )

    # Top winners/losers by symbol net
    ok = [r for r in rows if "error" not in r and int(r["overall"]["trade_count"]) > 0]
    top_winners = sorted(ok, key=lambda r: float(r["overall"]["net_profit"]), reverse=True)[:10]
    top_losers = sorted(ok, key=lambda r: float(r["overall"]["net_profit"]))[:10]

    long_trades = [t for t in all_trades if "LONG" in t["direction"]]
    short_trades = [t for t in all_trades if "SHORT" in t["direction"]]

    payload = {
        "generated_at": utc_now().isoformat(),
        "universe_note": (
            "Crypto market-cap top-N (no equity/stock feed available in this project)"
        ),
        "method": {
            "strategy": "old_rules",
            "lesson_skip_rules": [],
            "days": args.days,
            "top": args.top,
            "leverage": args.leverage,
            "start_capital": args.capital,
            "risk_per_trade_usd": settings.paper_risk_per_trade_usd,
            "max_notional_usd": settings.paper_max_notional_usd,
            "fee_percent": settings.paper_fee_percent,
            "timeframe": settings.primary_timeframe,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        },
        "per_symbol_books": per_symbol,
        "shared_portfolio": portfolio,
        "sides": {
            "long": {
                "trades": len(long_trades),
                "net_pnl": round(sum(t["net_pnl"] for t in long_trades), 2),
            },
            "short": {
                "trades": len(short_trades),
                "net_pnl": round(sum(t["net_pnl"] for t in short_trades), 2),
            },
        },
        "top_winners": [
            {
                "symbol": r["symbol"],
                "rank": r["rank"],
                "trades": int(r["overall"]["trade_count"]),
                "net": round(float(r["overall"]["net_profit"]), 2),
            }
            for r in top_winners
        ],
        "top_losers": [
            {
                "symbol": r["symbol"],
                "rank": r["rank"],
                "trades": int(r["overall"]["trade_count"]),
                "net": round(float(r["overall"]["net_profit"]), 2),
            }
            for r in top_losers
        ],
        "failed": [
            {"symbol": r["symbol"], "rank": r["rank"], "error": r["error"]}
            for r in rows
            if "error" in r
        ][:30],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"shared_portfolio": portfolio, "per_symbol_books": per_symbol}, indent=2))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
