"""Counterfactual indicator-weight sweep on paper trades.

Re-scores each linked signal from stored ``signal_score_components.raw_score``
with alternate ``StrategyWeights``, then checks whether the trade would still
pass paper gates (STRONG direction, min score, R:R, data quality). Trades that
fail are treated as skipped ($0); taken trades keep their recorded PnL.

Outputs JSON to stdout; use ``> exports/indicator_weights_sim.json``.
Does not mutate live config.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.container import build_container
from app.core.config import get_settings
from app.core.enums import ScoreCategory, SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.models.paper import PaperPosition
from app.models.signal import Signal, SignalScoreComponent
from app.repositories.paper_repository import PaperRepository
from app.signals.engine import (
    LONG_SCORE,
    SHORT_SCORE,
    STRONG_AGREEMENT,
    STRONG_LONG_SCORE,
    STRONG_SHORT_SCORE,
    SignalEngine,
)
from app.signals.types import ScoreComponent
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

MIN_DATA_QUALITY = 60.0


@dataclass
class TradeRecord:
    position_id: int
    symbol: str
    direction: str
    opened_at: datetime
    closed_at: datetime | None
    status: str
    pnl: float
    signal_id: int | None
    stored_score: float | None
    stored_direction: str | None
    risk_reward_ratio: float | None
    data_quality: float | None
    has_levels: bool
    raw_scores: dict[str, float]


@dataclass
class VariantResult:
    key: str
    label: str
    weights: dict[str, float]
    n_taken: int
    n_skipped: int
    total_pnl: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    avg_pnl: float
    skip_reasons: dict[str, int]
    delta_vs_baseline: float | None = None


def _effective_weights(weights: StrategyWeights, *, enable_sentiment: bool) -> StrategyWeights:
    return weights if enable_sentiment else weights.without_sentiment()


def _recompute_score(raw_scores: dict[str, float], weights: StrategyWeights) -> tuple[float, float]:
    """Return (score 0..100, mtf_agreement -1..+1)."""
    wmap = weights.as_dict()
    components: list[ScoreComponent] = []
    for cat in ScoreCategory:
        raw = raw_scores.get(cat.value)
        if raw is None:
            continue
        weight = wmap.get(cat, 0.0)
        if weight <= 0:
            continue
        components.append(
            ScoreComponent(category=cat, raw_score=float(raw), weight=float(weight))
        )
    score = SignalEngine._weighted_score(components)
    agreement = SignalEngine._agreement_value(components)
    return score, agreement


def _recompute_direction(score: float, agreement: float) -> SignalDirection:
    return SignalEngine._determine_direction(score, agreement)


def _passes_gates(
    trade: TradeRecord,
    direction: SignalDirection,
    score: float,
    *,
    min_score: float,
    short_max_score: float,
    require_strong: bool,
    min_rr: float,
) -> tuple[bool, str | None]:
    if not trade.has_levels:
        return False, "missing_levels"
    dq = trade.data_quality if trade.data_quality is not None else 100.0
    if dq < MIN_DATA_QUALITY:
        return False, "low_data_quality"
    rr = trade.risk_reward_ratio if trade.risk_reward_ratio is not None else 0.0
    if rr < min_rr:
        return False, "low_rr"
    if not direction.is_actionable:
        return False, "not_actionable"
    if require_strong and direction not in {
        SignalDirection.STRONG_LONG,
        SignalDirection.STRONG_SHORT,
    }:
        return False, "not_strong"
    if direction.is_long and score < min_score:
        return False, "score_below_min"
    if direction.is_short and score > short_max_score:
        return False, "score_above_short_max"
    return True, None


def _max_drawdown(pnls_by_time: list[tuple[datetime, float]]) -> float:
    if not pnls_by_time:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for _, pnl in sorted(pnls_by_time, key=lambda x: x[0]):
        cum += pnl
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return round(max_dd, 2)


def _profit_factor(pnls: list[float]) -> float:
    wins = sum(p for p in pnls if p > 0)
    losses = sum(abs(p) for p in pnls if p < 0)
    if losses > 0:
        return round(wins / losses, 4)
    return 99.0 if wins > 0 else 0.0


def _shift_weight(
    base: StrategyWeights,
    *,
    boost: ScoreCategory,
    delta: float,
) -> StrategyWeights:
    """Increase one category by ``delta``, subtract proportionally from others (sentiment=0)."""
    d = {cat: val for cat, val in base.as_dict().items() if cat != ScoreCategory.SENTIMENT}
    d[boost] = d.get(boost, 0.0) + delta
    if d[boost] < 0 or d[boost] > 1.0:
        raise ValueError(f"Invalid boost for {boost.value}: {d[boost]}")
    others_total = sum(v for k, v in d.items() if k != boost)
    if others_total <= 0:
        raise ValueError("No weight left to redistribute")
    factor = (others_total - delta) / others_total
    for k in list(d):
        if k != boost:
            d[k] *= factor
    d[ScoreCategory.SENTIMENT] = 0.0
    return StrategyWeights(
        trend=d[ScoreCategory.TREND],
        momentum=d[ScoreCategory.MOMENTUM],
        volume=d[ScoreCategory.VOLUME],
        market_structure=d[ScoreCategory.MARKET_STRUCTURE],
        multi_timeframe=d[ScoreCategory.MULTI_TIMEFRAME],
        volatility=d[ScoreCategory.VOLATILITY],
        sentiment=0.0,
        risk_reward=d[ScoreCategory.RISK_REWARD],
    )


def _custom_weights(**kwargs: float) -> StrategyWeights:
    base = DEFAULT_WEIGHTS.without_sentiment().model_dump()
    base.update(kwargs)
    base["sentiment"] = 0.0
    return StrategyWeights(**base)


def _variant_catalog() -> list[tuple[str, str, StrategyWeights]]:
    baseline = DEFAULT_WEIGHTS.without_sentiment()
    variants: list[tuple[str, str, StrategyWeights]] = [
        ("baseline", "Baseline (current defaults, no sentiment)", baseline),
    ]

    boosts = [
        (ScoreCategory.TREND, "+8pp trend"),
        (ScoreCategory.MOMENTUM, "+8pp momentum"),
        (ScoreCategory.VOLUME, "+8pp volume"),
        (ScoreCategory.MARKET_STRUCTURE, "+8pp structure"),
        (ScoreCategory.MULTI_TIMEFRAME, "+8pp MTF"),
        (ScoreCategory.VOLATILITY, "+4pp volatility"),
        (ScoreCategory.RISK_REWARD, "+4pp risk_reward"),
    ]
    for cat, label in boosts:
        delta = 0.08 if cat not in {ScoreCategory.VOLATILITY, ScoreCategory.RISK_REWARD} else 0.04
        key = f"boost_{cat.value}"
        try:
            variants.append((key, label, _shift_weight(baseline, boost=cat, delta=delta)))
        except ValueError:
            pass

    reductions = [
        (ScoreCategory.TREND, "-5pp trend"),
        (ScoreCategory.MOMENTUM, "-5pp momentum"),
        (ScoreCategory.MULTI_TIMEFRAME, "-5pp MTF"),
        (ScoreCategory.MARKET_STRUCTURE, "-5pp structure"),
    ]
    for cat, label in reductions:
        key = f"reduce_{cat.value}"
        try:
            variants.append((key, label, _shift_weight(baseline, boost=cat, delta=-0.05)))
        except ValueError:
            pass

    combos = [
        (
            "trend_momentum_heavy",
            "Trend 30% + Momentum 28%",
            _custom_weights(trend=0.30, momentum=0.28, volume=0.12, market_structure=0.12, multi_timeframe=0.12, volatility=0.03, risk_reward=0.03),
        ),
        (
            "mtf_structure_heavy",
            "MTF 22% + Structure 22%",
            _custom_weights(trend=0.20, momentum=0.16, volume=0.12, market_structure=0.22, multi_timeframe=0.22, volatility=0.04, risk_reward=0.04),
        ),
        (
            "momentum_volume_heavy",
            "Momentum 28% + Volume 20%",
            _custom_weights(trend=0.20, momentum=0.28, volume=0.20, market_structure=0.12, multi_timeframe=0.12, volatility=0.04, risk_reward=0.04),
        ),
        (
            "trend_only_heavy",
            "Trend 35% (rest proportional)",
            _shift_weight(baseline, boost=ScoreCategory.TREND, delta=0.10),
        ),
        (
            "low_mtf_high_trend",
            "Trend 32%, MTF 8%",
            _custom_weights(trend=0.32, momentum=0.18, volume=0.14, market_structure=0.14, multi_timeframe=0.08, volatility=0.07, risk_reward=0.07),
        ),
        (
            "high_mtf_low_trend",
            "MTF 25%, Trend 15%",
            _custom_weights(trend=0.15, momentum=0.18, volume=0.14, market_structure=0.15, multi_timeframe=0.25, volatility=0.07, risk_reward=0.06),
        ),
        (
            "equal_core_five",
            "Equal 5 core @ 16% each",
            _custom_weights(trend=0.16, momentum=0.16, volume=0.16, market_structure=0.16, multi_timeframe=0.16, volatility=0.10, risk_reward=0.10),
        ),
        (
            "rr_heavy",
            "Risk/Reward 10%",
            _custom_weights(trend=0.23, momentum=0.18, volume=0.14, market_structure=0.14, multi_timeframe=0.14, volatility=0.07, risk_reward=0.10),
        ),
    ]
    variants.extend(combos)
    return variants


def _evaluate_variant(
    trades: list[TradeRecord],
    weights: StrategyWeights,
    *,
    min_score: float,
    short_max_score: float,
    require_strong: bool,
    min_rr: float,
    enable_sentiment: bool,
) -> VariantResult:
    effective = _effective_weights(weights, enable_sentiment=enable_sentiment)
    taken_pnls: list[float] = []
    pnls_by_time: list[tuple[datetime, float]] = []
    skip_reasons: dict[str, int] = {}

    for trade in trades:
        if not trade.raw_scores:
            skip_reasons["no_score_components"] = skip_reasons.get("no_score_components", 0) + 1
            continue
        score, agreement = _recompute_score(trade.raw_scores, effective)
        direction = _recompute_direction(score, agreement)
        ok, reason = _passes_gates(
            trade,
            direction,
            score,
            min_score=min_score,
            short_max_score=short_max_score,
            require_strong=require_strong,
            min_rr=min_rr,
        )
        if not ok:
            skip_reasons[reason or "filtered"] = skip_reasons.get(reason or "filtered", 0) + 1
            continue
        taken_pnls.append(trade.pnl)
        pnls_by_time.append((trade.opened_at, trade.pnl))

    n_taken = len(taken_pnls)
    n_skipped = len(trades) - n_taken
    wins = sum(1 for p in taken_pnls if p > 0)
    total = round(sum(taken_pnls), 2)

    return VariantResult(
        key="",
        label="",
        weights={k.value: round(v, 4) for k, v in effective.as_dict().items()},
        n_taken=n_taken,
        n_skipped=n_skipped,
        total_pnl=total,
        win_rate=round(wins / n_taken, 4) if n_taken else 0.0,
        profit_factor=_profit_factor(taken_pnls),
        max_drawdown=_max_drawdown(pnls_by_time),
        avg_pnl=round(total / n_taken, 2) if n_taken else 0.0,
        skip_reasons=skip_reasons,
    )


async def _load_trades(session) -> list[TradeRecord]:
    container = build_container(get_settings())
    account = await container.paper_trading.get_or_create_account(session)
    positions = await PaperRepository(session).list_positions(account.id)

    signal_ids = [p.signal_id for p in positions if p.signal_id]
    signal_map: dict[int, Signal] = {}
    if signal_ids:
        result = await session.execute(
            select(Signal)
            .where(Signal.id.in_(signal_ids))
            .options(selectinload(Signal.score_components))
        )
        signal_map = {int(s.id): s for s in result.scalars()}

    trades: list[TradeRecord] = []
    for p in positions:
        sig = signal_map.get(int(p.signal_id)) if p.signal_id else None
        raw_scores: dict[str, float] = {}
        if sig and sig.score_components:
            for comp in sig.score_components:
                raw_scores[str(comp.category)] = float(comp.raw_score)

        has_levels = all(
            getattr(sig, f, None) is not None
            for f in ("stop_loss", "take_profit_1", "take_profit_2", "take_profit_3")
        ) if sig else False

        trades.append(
            TradeRecord(
                position_id=int(p.id),
                symbol=p.symbol,
                direction=p.direction,
                opened_at=ensure_utc(p.opened_at),
                closed_at=ensure_utc(p.closed_at) if p.closed_at else None,
                status=p.status,
                pnl=float(p.realized_pnl),
                signal_id=int(p.signal_id) if p.signal_id else None,
                stored_score=float(sig.score) if sig else (float(p.signal_score) if p.signal_score else None),
                stored_direction=sig.direction if sig else None,
                risk_reward_ratio=float(sig.risk_reward_ratio) if sig and sig.risk_reward_ratio else None,
                data_quality=float(sig.data_quality) if sig else None,
                has_levels=has_levels,
                raw_scores=raw_scores,
            )
        )

    await container.aclose()
    trades.sort(key=lambda t: t.opened_at)
    return trades


def _score_fidelity(trades: list[TradeRecord], baseline_weights: StrategyWeights) -> dict[str, Any]:
    diffs: list[float] = []
    for t in trades:
        if t.stored_score is None or not t.raw_scores:
            continue
        score, _ = _recompute_score(t.raw_scores, baseline_weights)
        diffs.append(abs(score - t.stored_score))
    if not diffs:
        return {"n": 0, "mae": None, "max": None}
    return {"n": len(diffs), "mae": round(sum(diffs) / len(diffs), 3), "max": round(max(diffs), 3)}


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    import logging

    for name in ("app", "asyncio", "sqlalchemy"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = False
    logging.basicConfig(level=logging.ERROR, stream=sys.stderr, force=True)

    async with session_scope() as session:
        trades = await _load_trades(session)

    print(f"Loaded {len(trades)} paper positions", file=sys.stderr)
    with_components = sum(1 for t in trades if t.raw_scores)
    closed_pnl = round(sum(t.pnl for t in trades if t.status == "closed"), 2)
    print(f"  with score components: {with_components}, closed PnL: {closed_pnl:+.2f}", file=sys.stderr)

    baseline_w = DEFAULT_WEIGHTS.without_sentiment()
    fidelity = _score_fidelity(trades, baseline_w)

    catalog = _variant_catalog()
    results: list[VariantResult] = []
    baseline_pnl: float | None = None

    for key, label, weights in catalog:
        vr = _evaluate_variant(
            trades,
            weights,
            min_score=settings.signal_min_score,
            short_max_score=settings.signal_short_max_score,
            require_strong=settings.signal_require_strong,
            min_rr=settings.min_risk_reward_ratio,
            enable_sentiment=settings.enable_sentiment,
        )
        vr.key = key
        vr.label = label
        results.append(vr)
        if key == "baseline":
            baseline_pnl = vr.total_pnl

    for vr in results:
        if baseline_pnl is not None:
            vr.delta_vs_baseline = round(vr.total_pnl - baseline_pnl, 2)

    ranked = sorted(results, key=lambda r: r.total_pnl, reverse=True)
    winners = [r for r in ranked if r.key != "baseline" and (r.delta_vs_baseline or 0) > 0]

    # Category impact: average raw score of taken vs skipped at baseline
    baseline_eval = next(r for r in results if r.key == "baseline")
    category_stats: dict[str, dict[str, float]] = {}
    for cat in ScoreCategory:
        if cat == ScoreCategory.SENTIMENT:
            continue
        taken_raw: list[float] = []
        all_raw: list[float] = []
        for t in trades:
            if cat.value in t.raw_scores:
                all_raw.append(t.raw_scores[cat.value])
                if t.pnl != 0:  # rough; full pass logic omitted for brevity
                    taken_raw.append(t.raw_scores[cat.value])
        if all_raw:
            category_stats[cat.value] = {
                "avg_raw_all": round(sum(all_raw) / len(all_raw), 2),
                "avg_raw_winners": round(
                    sum(t.raw_scores.get(cat.value, 0) for t in trades if t.pnl > 0)
                    / max(1, sum(1 for t in trades if t.pnl > 0)),
                    2,
                ),
                "avg_raw_losers": round(
                    sum(t.raw_scores.get(cat.value, 0) for t in trades if t.pnl < 0)
                    / max(1, sum(1 for t in trades if t.pnl < 0)),
                    2,
                ),
            }

    payload: dict[str, Any] = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "type": "signal_filter_counterfactual",
            "description": (
                "Re-score stored raw_score components with alternate weights; "
                "keep recorded PnL for trades that still pass paper gates"
            ),
            "gates": {
                "min_score": settings.signal_min_score,
                "short_max_score": settings.signal_short_max_score,
                "require_strong": settings.signal_require_strong,
                "min_rr": settings.min_risk_reward_ratio,
                "min_data_quality": MIN_DATA_QUALITY,
            },
            "default_weights": {k.value: round(v, 4) for k, v in baseline_w.as_dict().items()},
            "direction_thresholds": {
                "strong_long": STRONG_LONG_SCORE,
                "long": LONG_SCORE,
                "short": SHORT_SCORE,
                "strong_short": STRONG_SHORT_SCORE,
                "strong_agreement": STRONG_AGREEMENT,
            },
            "caveats": [
                "Small paper sample (~100 trades) — high overfitting risk",
                "Raw scores fixed at signal time; no indicator recompute",
                "NO_TRADE filters (ADX/RSI/range) not re-evaluated — uses stored gate metadata only",
                "PnL is actual paper outcome, not re-simulated exits",
            ],
        },
        "sample": {
            "n_positions": len(trades),
            "n_closed": sum(1 for t in trades if t.status == "closed"),
            "n_with_components": with_components,
            "recorded_pnl_all_closed": closed_pnl,
            "first_open": min((t.opened_at for t in trades), default=None),
            "last_open": max((t.opened_at for t in trades), default=None),
        },
        "fidelity": fidelity,
        "baseline": asdict(baseline_eval),
        "variants": [asdict(r) for r in results],
        "ranked": [
            {
                "rank": i + 1,
                "key": r.key,
                "label": r.label,
                "total_pnl": r.total_pnl,
                "delta_vs_baseline": r.delta_vs_baseline,
                "n_taken": r.n_taken,
                "n_skipped": r.n_skipped,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
                "max_drawdown": r.max_drawdown,
            }
            for i, r in enumerate(ranked)
        ],
        "best_variant": asdict(ranked[0]) if ranked else None,
        "top_improvements": [asdict(r) for r in winners[:5]],
        "category_stats": category_stats,
        "recommendations": [],
    }

    for k in ("first_open", "last_open"):
        v = payload["sample"][k]
        if isinstance(v, datetime):
            payload["sample"][k] = v.isoformat()

    if winners:
        best = winners[0]
        payload["recommendations"] = [
            f"Best filter-improving variant: {best.label} ({best.key})",
            f"PnL delta vs baseline: {best.delta_vs_baseline:+.2f} USD",
            f"Trades taken: {best.n_taken} vs baseline {baseline_eval.n_taken}",
        ]

    print(json.dumps(payload, indent=2, default=str))
    print(
        f"BASELINE pnl={baseline_pnl:+.2f} n={baseline_eval.n_taken} | "
        + " | ".join(
            f"{r.key}={r.total_pnl:+.2f} (Δ{r.delta_vs_baseline:+.2f}, n={r.n_taken})"
            for r in ranked[:5]
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
