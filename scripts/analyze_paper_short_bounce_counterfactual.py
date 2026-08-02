"""Counterfactual: new short bounce / thin-volume gates vs closed paper trades.

For each closed short paper position, recompute primary-TF indicators at signal
time (fallback: open time) and ask whether the new NO_TRADE gates would have
blocked the entry. Reports actual PnL vs PnL after removing blocked trades.

    python scripts/analyze_paper_short_bounce_counterfactual.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.core.time import ensure_utc, utc_now  # noqa: E402
from app.database.session import session_scope  # noqa: E402
from app.indicators.engine import IndicatorEngine  # noqa: E402
from app.signals.engine import SignalEngine, SignalEngineConfig  # noqa: E402

BINANCE = "https://api.binance.com"
WARMUP_BARS = 220


def _fetch_binance_1h(symbol: str, start, end) -> pd.DataFrame:
    rows: list[list] = []
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    with httpx.Client(timeout=45.0) as client:
        while cursor < end_ms:
            resp = client.get(
                f"{BINANCE}/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": "1h",
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            if resp.status_code >= 400:
                break
            batch = resp.json() or []
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1][0]) + 1
            if len(batch) < 1000:
                break
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    idx = pd.to_datetime([r[0] for r in rows], unit="ms", utc=True)
    df = pd.DataFrame(
        {
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
            "volume": [float(r[5]) for r in rows],
        },
        index=idx,
    )
    return df[~df.index.duplicated(keep="last")].sort_index()


async def _load_closed_shorts() -> list[dict[str, Any]]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(
                    """
                    select p.id, p.symbol, p.direction, p.signal_score, p.realized_pnl,
                           p.risk_amount, p.exit_reason, p.opened_at, p.closed_at,
                           p.signal_id, p.timeframe,
                           s.created_at as signal_created_at, s.score as signal_score_live,
                           s.primary_timeframe
                    from paper_positions p
                    left join signals s on s.id = p.signal_id
                    where p.status = 'closed'
                      and p.direction in ('SHORT', 'STRONG_SHORT')
                    order by p.opened_at
                    """
                )
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def _load_db_1h(symbol: str, start, end) -> pd.DataFrame | None:
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(
                    """
                    select c.open_time, c.open, c.high, c.low, c.close, c.volume
                    from market_candles c
                    join assets a on a.id = c.asset_id
                    where a.symbol = :symbol
                      and c.timeframe = '1h'
                      and c.open_time >= :start
                      and c.open_time <= :end
                    order by c.open_time
                    """
                ),
                {"symbol": symbol, "start": start, "end": end},
            )
        ).mappings().all()
    if not rows:
        return None
    df = pd.DataFrame([dict(r) for r in rows]).set_index("open_time")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df


async def _frame_for(
    symbol: str,
    asof,
    cache: dict[str, pd.DataFrame],
    sources: dict[str, str],
) -> pd.DataFrame | None:
    if symbol in cache:
        return cache[symbol]
    start = asof - timedelta(hours=WARMUP_BARS + 48)
    end = asof + timedelta(hours=2)
    df = await _load_db_1h(symbol, start, end)
    source = "db"
    if df is None or len(df) < 60:
        df = await asyncio.to_thread(_fetch_binance_1h, symbol, start, end)
        source = "binance"
    if df is None or df.empty:
        cache[symbol] = pd.DataFrame()
        sources[symbol] = "none"
        return None
    cache[symbol] = df
    sources[symbol] = source
    return df


def _gate_trade(
    engine: SignalEngine,
    indicators_engine: IndicatorEngine,
    frame: pd.DataFrame,
    *,
    symbol: str,
    asof,
) -> dict[str, Any]:
    window = frame.loc[frame.index <= asof]
    if len(window) < 60:
        return {
            "blocked": None,
            "reason": "insufficient_candles",
            "rsi": None,
            "rsi_recent_low": None,
            "volume_ratio": None,
            "bounce_pts": None,
        }
    ind = indicators_engine.compute(window, "1h", symbol=symbol, strict=False)
    reason = engine._short_bounce_block_reason(ind)
    bounce = None
    if ind.rsi_14 is not None and ind.rsi_recent_low is not None:
        bounce = round(ind.rsi_14 - ind.rsi_recent_low, 2)
    return {
        "blocked": reason is not None,
        "reason": reason,
        "rsi": ind.rsi_14,
        "rsi_recent_low": ind.rsi_recent_low,
        "volume_ratio": ind.volume_ratio,
        "bounce_pts": bounce,
    }


async def run(out: Path) -> dict[str, Any]:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    trades = await _load_closed_shorts()
    engine = SignalEngine(
        SignalEngineConfig(
            short_bounce_block_enabled=settings.signal_short_bounce_block_enabled,
            short_rsi_extreme=settings.signal_short_rsi_extreme,
            short_rsi_bounce_points=settings.signal_short_rsi_bounce_points,
            short_min_volume_ratio=settings.signal_short_min_volume_ratio,
        )
    )
    indicators_engine = IndicatorEngine(min_candles=50)
    cache: dict[str, pd.DataFrame] = {}
    sources: dict[str, str] = {}

    rows: list[dict[str, Any]] = []
    for idx, trade in enumerate(trades, start=1):
        symbol = str(trade["symbol"]).upper()
        asof = ensure_utc(trade["signal_created_at"] or trade["opened_at"])
        print(f"[{idx}/{len(trades)}] {symbol} #{trade['id']} @ {asof.isoformat()}", flush=True)
        frame = await _frame_for(symbol, asof, cache, sources)
        if frame is None or frame.empty:
            gate = {
                "blocked": None,
                "reason": "no_candles",
                "rsi": None,
                "rsi_recent_low": None,
                "volume_ratio": None,
                "bounce_pts": None,
            }
            source = "none"
        else:
            gate = _gate_trade(
                engine, indicators_engine, frame, symbol=symbol, asof=asof
            )
            source = sources.get(symbol, "db")
        pnl = float(trade["realized_pnl"] or 0.0)
        risk = float(trade["risk_amount"] or 0.0)
        r_mult = (pnl / risk) if risk > 0 else 0.0
        rows.append(
            {
                "id": int(trade["id"]),
                "symbol": symbol,
                "direction": trade["direction"],
                "score": float(trade["signal_score"] or 0.0),
                "pnl": round(pnl, 2),
                "r": round(r_mult, 3),
                "exit_reason": trade["exit_reason"],
                "opened_at": ensure_utc(trade["opened_at"]).isoformat(),
                "asof": asof.isoformat(),
                "candle_source": source,
                **gate,
            }
        )

    evaluated = [r for r in rows if r["blocked"] is not None]
    blocked = [r for r in evaluated if r["blocked"]]
    kept = [r for r in evaluated if not r["blocked"]]
    unknown = [r for r in rows if r["blocked"] is None]

    actual_net = round(sum(r["pnl"] for r in rows), 2)
    blocked_net = round(sum(r["pnl"] for r in blocked), 2)
    kept_net = round(sum(r["pnl"] for r in kept), 2)
    unknown_net = round(sum(r["pnl"] for r in unknown), 2)
    # Counterfactual: remove blocked trades; keep unknowns as-is (conservative).
    cf_net = round(kept_net + unknown_net, 2)

    bounce_blocks = [r for r in blocked if r["reason"] and "bounced" in r["reason"].lower()]
    volume_blocks = [r for r in blocked if r["reason"] and "thin volume" in r["reason"].lower()]

    def _wr(items: list[dict[str, Any]]) -> float:
        if not items:
            return 0.0
        wins = sum(1 for r in items if r["pnl"] > 0)
        return round(wins / len(items), 4)

    payload = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "gate": "short_bounce_block + thin_volume",
            "rsi_extreme": settings.signal_short_rsi_extreme,
            "bounce_points": settings.signal_short_rsi_bounce_points,
            "min_volume_ratio": settings.signal_short_min_volume_ratio,
            "asof": "signal.created_at else position.opened_at",
            "note": (
                "Counterfactual removes trades the new gates would block. "
                "Trades without candles stay in the book (unknown)."
            ),
        },
        "actual": {
            "trades": len(rows),
            "net_pnl": actual_net,
            "win_rate": _wr(rows),
            "wins": sum(1 for r in rows if r["pnl"] > 0),
            "losses": sum(1 for r in rows if r["pnl"] <= 0),
        },
        "counterfactual": {
            "trades_kept": len(kept) + len(unknown),
            "trades_blocked": len(blocked),
            "trades_unknown": len(unknown),
            "net_pnl": cf_net,
            "win_rate_kept_evaluated": _wr(kept),
            "blocked_net_pnl_removed": blocked_net,
            "delta_vs_actual": round(cf_net - actual_net, 2),
            "blocked_by_bounce": len(bounce_blocks),
            "blocked_by_thin_volume": len(volume_blocks),
            "blocked_bounce_pnl": round(sum(r["pnl"] for r in bounce_blocks), 2),
            "blocked_volume_pnl": round(sum(r["pnl"] for r in volume_blocks), 2),
        },
        "blocked_trades": sorted(blocked, key=lambda r: r["pnl"]),
        "kept_trades": kept,
        "unknown_trades": unknown,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    summary = {
        "actual_net": actual_net,
        "counterfactual_net": cf_net,
        "delta": payload["counterfactual"]["delta_vs_actual"],
        "blocked": len(blocked),
        "kept_evaluated": len(kept),
        "unknown": len(unknown),
        "blocked_bounce": len(bounce_blocks),
        "blocked_volume": len(volume_blocks),
        "out": str(out),
    }
    print(json.dumps(summary, indent=2))
    print("\nTop blocked (worst first):")
    for r in sorted(blocked, key=lambda x: x["pnl"])[:15]:
        print(
            f"  #{r['id']} {r['symbol']:12} pnl={r['pnl']:+8.2f} "
            f"rsi={r['rsi']} low={r['rsi_recent_low']} bounce={r['bounce_pts']} "
            f"vol={r['volume_ratio']} | {(r['reason'] or '')[:70]}"
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "exports" / "paper_short_bounce_counterfactual.json",
    )
    args = parser.parse_args()
    asyncio.run(run(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
