"""Counterfactual Scenario-B rebuild with coin weights v1 → v2 (MTF/Structure 18/18).

Isolates the weight change: recompute coin score from stored raw components under
v2 weights, then remap the stored final score so the market-blend side stays fixed.

Never rebuilds account ``default``. Temporarily patches signal scores, restores after.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.container import build_container
from app.core.config import get_settings
from app.core.enums import ScoreCategory, SignalDirection
from app.core.logging import configure_logging
from app.database.session import session_scope
from app.models.market import Asset
from app.models.signal import Signal
from app.repositories.paper_repository import PaperRepository
from app.signals.engine import SignalEngine
from app.signals.types import ScoreComponent
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

SINCE = datetime(2026, 7, 31, 16, 32, 35, tzinfo=timezone.utc)
OUT = Path("/tmp/sim_weights_18_18.json")
BACKUP = Path("/tmp/sim_weights_18_18_backup.json")
ACCT_BASE = "sim_w_baseline_stored"
ACCT_NEW = "sim_w_18_18"

# Strategy version 1 (pre 18/18) — from DB
OLD_WEIGHTS = StrategyWeights(
    trend=0.2730,
    momentum=0.2184,
    volume=0.1638,
    market_structure=0.1638,
    multi_timeframe=0.1046,
    volatility=0.0437,
    sentiment=0.0,
    risk_reward=0.0327,
)
NEW_WEIGHTS = DEFAULT_WEIGHTS  # v2: structure/mtf 18%
COIN_BLEND_W = 0.60


def _coin_from_components(signal: Signal, weights: StrategyWeights) -> tuple[float, str]:
    wmap = weights.as_dict()
    components: list[ScoreComponent] = []
    for comp in signal.score_components:
        cat = ScoreCategory(comp.category)
        raw = float(comp.raw_score)
        components.append(
            ScoreComponent(category=cat, raw_score=raw, weight=float(wmap.get(cat, 0.0)))
        )
    score = SignalEngine._weighted_score(components)
    agreement = SignalEngine._agreement_value(components)
    direction = SignalEngine._determine_direction(score, agreement)
    return score, direction.value


def _stored_coin(signal: Signal, fallback_coin: float) -> float:
    ctx = signal.market_context if isinstance(signal.market_context, dict) else {}
    blend = ctx.get("blend") if isinstance(ctx.get("blend"), dict) else {}
    if blend.get("coinScore") is not None:
        return float(blend["coinScore"])
    if ctx.get("coinScore") is not None:
        return float(ctx["coinScore"])
    return float(fallback_coin)


def _remap_final(
    *,
    old_final: float,
    old_coin: float,
    new_coin: float,
    direction: str,
    w_coin: float = COIN_BLEND_W,
) -> float:
    """Keep market-side contribution; swap coin contribution under same blend weight."""
    try:
        dir_enum = SignalDirection(direction)
    except ValueError:
        return max(0.0, min(100.0, new_coin))
    for_short = dir_enum.is_short
    old_coin_q = (100.0 - old_coin) if for_short else old_coin
    old_final_q = (100.0 - old_final) if for_short else old_final
    new_coin_q = (100.0 - new_coin) if for_short else new_coin
    market_q = old_final_q - w_coin * old_coin_q
    new_final_q = w_coin * new_coin_q + market_q
    new_final = (100.0 - new_final_q) if for_short else new_final_q
    return round(max(0.0, min(100.0, new_final)), 4)


async def _top400() -> set[str]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Asset.symbol)
                .where(
                    Asset.in_universe.is_(True),
                    Asset.is_active.is_(True),
                    Asset.market_cap_rank.is_not(None),
                )
                .order_by(Asset.market_cap_rank.asc())
                .limit(400)
            )
        ).scalars().all()
    return {str(s).upper() for s in rows}


async def _desk_kpi() -> dict:
    settings = get_settings()
    container = build_container(settings)
    paper = container.paper_trading
    try:
        async with session_scope() as session:
            summary = await paper.summary(session)
    finally:
        await container.aclose()
    return {
        "equity": round(float(summary.equity), 2),
        "realized": round(float(summary.realized_pnl), 2),
        "closed": int(summary.closed_trades),
        "open": int(summary.open_positions),
        "pending": int(summary.pending_positions),
        "win_rate": round(float(summary.win_rate) * 100, 1),
        "profit_factor": round(float(summary.profit_factor), 3),
    }


async def _backup_and_apply_new_scores() -> dict:
    stats = {
        "total": 0,
        "updated": 0,
        "no_components": 0,
        "direction_changed": 0,
        "avg_coin_delta": 0.0,
        "avg_final_delta": 0.0,
        "short_still_in_band": 0,
        "short_left_band": 0,
        "short_entered_band": 0,
    }
    backup: list[dict] = []
    coin_deltas: list[float] = []
    final_deltas: list[float] = []
    settings = get_settings()
    smin = float(settings.signal_short_min_score)
    smax = float(settings.signal_short_max_score)

    async with session_scope() as session:
        result = await session.execute(
            select(Signal)
            .options(selectinload(Signal.score_components))
            .where(Signal.created_at >= SINCE)
            .order_by(Signal.id)
        )
        signals = list(result.scalars().all())
        stats["total"] = len(signals)

        for signal in signals:
            backup.append(
                {
                    "id": signal.id,
                    "score": float(signal.score),
                    "direction": signal.direction,
                    "market_context": signal.market_context
                    if isinstance(signal.market_context, dict)
                    else None,
                }
            )
            if not signal.score_components:
                stats["no_components"] += 1
                continue

            old_final = float(signal.score)
            old_dir = signal.direction
            old_coin_v1, _ = _coin_from_components(signal, OLD_WEIGHTS)
            new_coin, new_dir = _coin_from_components(signal, NEW_WEIGHTS)
            # Prefer stored blend coinScore when present (true pre-blend coin).
            stored_coin = _stored_coin(signal, old_coin_v1)
            # If stored coin is closer to v1 recompute, use stored; else use v1 recompute.
            old_coin = stored_coin
            new_final = _remap_final(
                old_final=old_final,
                old_coin=old_coin,
                new_coin=new_coin,
                direction=old_dir if old_dir == new_dir else new_dir,
            )

            was_short_band = old_dir in {"SHORT", "STRONG_SHORT"} and smin <= old_final <= smax
            now_short_band = new_dir in {"SHORT", "STRONG_SHORT"} and smin <= new_final <= smax
            if was_short_band and now_short_band:
                stats["short_still_in_band"] += 1
            elif was_short_band and not now_short_band:
                stats["short_left_band"] += 1
            elif (not was_short_band) and now_short_band:
                stats["short_entered_band"] += 1

            stats["updated"] += 1
            coin_deltas.append(new_coin - old_coin)
            final_deltas.append(new_final - old_final)
            if new_dir != old_dir:
                stats["direction_changed"] += 1

            signal.score = new_final
            signal.direction = new_dir
            ctx = dict(signal.market_context) if isinstance(signal.market_context, dict) else {}
            ctx["coinScore"] = round(new_coin, 2)
            blend = dict(ctx.get("blend") or {}) if isinstance(ctx.get("blend"), dict) else {}
            blend["coinScore"] = round(new_coin, 2)
            blend["finalScore"] = round(new_final, 2)
            blend["source"] = "sim_weights_18_18_remap"
            ctx["blend"] = blend
            signal.market_context = ctx
            flag_modified(signal, "market_context")

        if coin_deltas:
            stats["avg_coin_delta"] = round(sum(coin_deltas) / len(coin_deltas), 3)
        if final_deltas:
            stats["avg_final_delta"] = round(sum(final_deltas) / len(final_deltas), 3)

    BACKUP.write_text(json.dumps(backup), encoding="utf-8")
    stats["backup_n"] = len(backup)
    return stats


async def _restore_backup() -> int:
    if not BACKUP.exists():
        return 0
    backup = json.loads(BACKUP.read_text(encoding="utf-8"))
    by_id = {int(r["id"]): r for r in backup}
    restored = 0
    # chunk to avoid huge IN lists
    ids = list(by_id.keys())
    async with session_scope() as session:
        for i in range(0, len(ids), 2000):
            chunk = ids[i : i + 2000]
            result = await session.execute(select(Signal).where(Signal.id.in_(chunk)))
            for signal in result.scalars().all():
                row = by_id[signal.id]
                signal.score = row["score"]
                signal.direction = row["direction"]
                signal.market_context = row["market_context"]
                flag_modified(signal, "market_context")
                restored += 1
    return restored


async def _run_sim(paper, provider, *, name: str, symbols: set[str]) -> dict:
    orig_goa = paper.get_or_create_account
    async with session_scope() as session:
        repo = PaperRepository(session)
        account = await repo.get_or_create_account(
            name=name,
            initial_balance=Decimal(str(paper._settings.paper_initial_balance)),
            margin_per_trade=Decimal(str(paper._settings.paper_margin_per_trade)),
            leverage=float(paper._settings.paper_leverage),
        )

        async def _goa(_s):
            return account

        paper.get_or_create_account = _goa  # type: ignore[method-assign]
        try:
            result = await paper.rebuild_from_signals(
                session,
                since=SINCE,
                provider=provider,
                providers=None,
                dispatched_only=False,
                one_per_symbol=False,
                symbols=symbols,
            )
            summary = await paper.summary(session)
        finally:
            paper.get_or_create_account = orig_goa  # type: ignore[method-assign]

    return {
        "name": name,
        "since": SINCE.isoformat(),
        "symbols": "top400",
        "equity": round(float(summary.equity), 2),
        "realized": round(float(summary.realized_pnl), 2),
        "closed": int(summary.closed_trades),
        "open": int(summary.open_positions),
        "pending": int(summary.pending_positions),
        "win_rate": round(float(summary.win_rate) * 100, 1),
        "profit_factor": round(float(summary.profit_factor), 3),
        "retest_filled": int(result.retest_filled),
        "retest_skipped": int(result.retest_skipped),
        "still_open": int(result.still_open),
    }


async def main() -> None:
    settings = get_settings()
    configure_logging("WARNING", json_output=False)
    print("OLD_WEIGHTS", OLD_WEIGHTS.model_dump(), flush=True)
    print("NEW_WEIGHTS", NEW_WEIGHTS.model_dump(), flush=True)

    desk = await _desk_kpi()
    print("DESK", json.dumps(desk), flush=True)

    top400 = await _top400()
    print(f"top400={len(top400)}", flush=True)

    container = build_container(settings)
    paper = container.paper_trading
    provider = container.paper_price_provider

    print(f"BASELINE sim ({ACCT_BASE}) with STORED scores ...", flush=True)
    baseline = await _run_sim(paper, provider, name=ACCT_BASE, symbols=top400)
    print("BASELINE", json.dumps(baseline), flush=True)

    score_stats = {}
    sim_new = {}
    restored = 0
    try:
        print("APPLY v2 18/18 remapped scores ...", flush=True)
        score_stats = await _backup_and_apply_new_scores()
        print("SCORE", json.dumps(score_stats), flush=True)

        print(f"NEW sim ({ACCT_NEW}) with 18/18 scores ...", flush=True)
        sim_new = await _run_sim(paper, provider, name=ACCT_NEW, symbols=top400)
        print("NEW", json.dumps(sim_new), flush=True)
    finally:
        print("RESTORE signals ...", flush=True)
        restored = await _restore_backup()
        print(f"restored={restored}", flush=True)

    desk_after = await _desk_kpi()
    await container.aclose()

    out = {
        "desk_live": desk,
        "desk_after_restore": desk_after,
        "baseline_stored_scores": baseline,
        "sim_18_18": sim_new,
        "score_stats": score_stats,
        "restored_signals": restored,
        "delta_vs_baseline_sim": {
            "equity": round(sim_new["equity"] - baseline["equity"], 2),
            "win_rate_pp": round(sim_new["win_rate"] - baseline["win_rate"], 1),
            "closed": sim_new["closed"] - baseline["closed"],
            "profit_factor": round(sim_new["profit_factor"] - baseline["profit_factor"], 3),
        },
        "delta_vs_desk": {
            "equity": round(sim_new["equity"] - desk["equity"], 2),
            "win_rate_pp": round(sim_new["win_rate"] - desk["win_rate"], 1),
            "closed": sim_new["closed"] - desk["closed"],
        },
        "notes": [
            "Both sims: Jul31 16:32, Top400, short 18-30, retest on, all-signals.",
            "Baseline uses current stored scores (what Scenario B used).",
            "18/18 remaps coin via v2 weights; market blend contribution held fixed.",
            "Live default ledger untouched; signal rows restored after.",
        ],
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2), flush=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
