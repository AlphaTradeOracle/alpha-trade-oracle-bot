"""Retrospective Market Regime soft-blend on persisted signals.

For each actionable signal since ``--since``, recompute BTC MTF regime at
``created_at`` and replace ``signals.score`` with the blended final score.
Stores ``coinScore`` + blend metadata under ``signals.market_context``.

Run before ``paper rebuild`` so short_max / long_min gates see soft-blend scores.

Usage:
  python scripts/rescore_signals_regime_soft_blend.py --since 2026-07-31T00:00:00+00:00
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging, get_logger
from app.core.time import ensure_utc
from app.database.session import session_scope
from app.market_regime import MarketRegimeEngine
from app.market_regime.score import FinalScoreCalculator
from app.market_regime.types import ScoreWeights
from app.models.signal import Signal

logger = get_logger(__name__)
BINANCE = "https://api.binance.com"
TFS = ("1h", "4h", "1d", "1w")


def _parse_since(raw: str) -> datetime:
    return ensure_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))


def _fetch_klines(symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    rows: list[list] = []
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    with httpx.Client(timeout=45.0) as client:
        while cursor < end_ms:
            resp = client.get(
                f"{BINANCE}/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            resp.raise_for_status()
            batch = resp.json()
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


def _coin_score(signal: Signal) -> float:
    ctx = signal.market_context if isinstance(signal.market_context, dict) else {}
    blend = ctx.get("blend") if isinstance(ctx.get("blend"), dict) else {}
    if blend.get("coinScore") is not None:
        return float(blend["coinScore"])
    if ctx.get("coinScore") is not None:
        return float(ctx["coinScore"])
    return float(signal.score)


async def run(*, since: datetime, dry_run: bool = False) -> dict:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    engine = MarketRegimeEngine(settings)
    calc = FinalScoreCalculator(
        ScoreWeights(
            coin=float(settings.market_score_weight_coin),
            global_market=float(settings.market_score_weight_global),
            funding=0.0,
            open_interest=0.0,
            liquidations=0.0,
        )
    )

    stats = {
        "since": since.isoformat(),
        "total": 0,
        "updated": 0,
        "skipped_direction": 0,
        "dry_run": dry_run,
        "avg_delta": 0.0,
        "blocked_short_max_after": 0,
        "short_max": float(settings.signal_short_max_score),
    }
    deltas: list[float] = []

    async with session_scope() as session:
        result = await session.execute(
            select(Signal)
            .where(Signal.created_at >= since)
            .order_by(Signal.created_at.asc())
        )
        signals = list(result.scalars().all())
        stats["total"] = len(signals)
        if not signals:
            logger.info("rescore_regime_blend_empty", **stats)
            return stats

        opens = [ensure_utc(s.created_at) for s in signals]
        start = min(opens) - timedelta(days=220)
        end = max(opens) + timedelta(hours=4)
        logger.info("rescore_regime_fetch_btc", start=start.isoformat(), end=end.isoformat())
        frames = {tf: _fetch_klines("BTCUSDT", tf, start, end) for tf in TFS}
        for tf, df in frames.items():
            logger.info("rescore_regime_btc_bars", timeframe=tf, bars=len(df))

        short_max = float(settings.signal_short_max_score)
        for signal in signals:
            try:
                direction = SignalDirection(signal.direction)
            except ValueError:
                stats["skipped_direction"] += 1
                continue
            if not direction.is_actionable:
                stats["skipped_direction"] += 1
                continue

            cutoff = ensure_utc(signal.created_at)
            sliced = {
                tf: df.loc[df.index <= cutoff]
                for tf, df in frames.items()
                if len(df.loc[df.index <= cutoff]) >= 50
            }
            snap = engine.resolve_from_btc_frames(sliced)
            coin = _coin_score(signal)
            blended = calc.blend(coin, direction, snap)
            old = float(signal.score)
            new = float(blended.final_score)
            deltas.append(new - old)

            if direction.is_short and new > short_max:
                stats["blocked_short_max_after"] += 1

            if abs(new - old) < 1e-6 and isinstance(signal.market_context, dict):
                blend_meta = signal.market_context.get("blend")
                if isinstance(blend_meta, dict) and blend_meta.get("finalScore") is not None:
                    continue

            stats["updated"] += 1
            if dry_run:
                continue

            ctx = dict(signal.market_context) if isinstance(signal.market_context, dict) else {}
            ctx["bias"] = snap.bias.value if snap.available else None
            ctx["biasLabel"] = snap.bias.label if snap.available else None
            ctx["globalScore"] = round(snap.global_score, 2) if snap.available else None
            ctx["coinScore"] = round(coin, 2)
            ctx["blend"] = {
                "coinScore": blended.coin_score,
                "finalScore": blended.final_score,
                "globalScore": blended.global_score,
                "fundingScore": blended.funding_score,
                "oiScore": blended.oi_score,
                "liquidationScore": blended.liquidation_score,
                "weights": blended.weights_used,
                "detail": blended.detail,
                "source": "rescore_signals_regime_soft_blend",
            }
            signal.score = new
            signal.market_context = ctx
            flag_modified(signal, "market_context")

        if deltas:
            stats["avg_delta"] = round(sum(deltas) / len(deltas), 3)

    logger.info("rescore_regime_blend_done", **stats)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="ISO UTC lower bound for signals")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    stats = asyncio.run(run(since=_parse_since(args.since), dry_run=args.dry_run))
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
