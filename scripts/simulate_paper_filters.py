"""Counterfactual paper-filter sweep on recorded MTF v2 paper trades.

Applies alternate gate thresholds (score, ADX, R:R, data quality, retest vs IST)
to closed paper positions. Trades that fail a variant's gates count as skipped ($0);
taken trades keep their recorded realized PnL.

Outputs JSON to stdout; redirect to ``exports/paper_filters_sim.json``.
Does not mutate live config.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.container import build_container
from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.models.market import IndicatorSnapshot
from app.models.paper import PaperPosition
from app.models.signal import Signal
from app.repositories.paper_repository import PaperRepository


@dataclass
class FilterConfig:
    min_score: float = 75.0
    short_max_score: float = 25.0
    require_strong: bool = True
    min_rr: float = 2.0
    min_data_quality: float = 60.0
    min_adx: float = 20.0
    ist_only: bool = False  # True = skip retest fills (IST immediate entry only)
    retest_enabled: bool = True  # False with ist_only=False keeps all entry modes


@dataclass
class TradeRecord:
    position_id: int
    symbol: str
    direction: str
    status: str
    timeframe: str
    pnl: float
    exit_reason: str | None
    opened_at: datetime
    closed_at: datetime | None
    signal_id: int | None
    score: float | None
    data_quality: float | None
    risk_reward_ratio: float | None
    adx: float | None
    market_phase: str | None
    is_retest_fill: bool
    is_strong: bool
    is_long: bool


@dataclass
class VariantResult:
    key: str
    label: str
    group: str  # baseline | single | combo
    filters: dict[str, Any]
    n_taken: int
    n_skipped: int
    total_pnl: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    avg_pnl: float
    skip_reasons: dict[str, int] = field(default_factory=dict)
    delta_pnl_vs_baseline: float | None = None
    delta_wr_vs_baseline: float | None = None
    delta_pf_vs_baseline: float | None = None


def _short_max_for(min_score: float) -> float:
    return round(100.0 - min_score, 2)


def _is_retest_fill(notes: str | None) -> bool:
    if not notes:
        return False
    return "retest_filled" in notes


def _passes_gates(trade: TradeRecord, cfg: FilterConfig) -> tuple[bool, str | None]:
    if trade.status != "closed":
        return False, "not_closed"

    if cfg.require_strong and not trade.is_strong:
        return False, "not_strong"

    if trade.score is None:
        return False, "missing_score"

    if trade.is_long:
        if trade.score < cfg.min_score:
            return False, "score_below_min"
    else:
        if trade.score > cfg.short_max_score:
            return False, "score_above_short_max"

    rr = trade.risk_reward_ratio if trade.risk_reward_ratio is not None else 0.0
    if rr < cfg.min_rr:
        return False, "low_rr"

    dq = trade.data_quality if trade.data_quality is not None else 100.0
    if dq < cfg.min_data_quality:
        return False, "low_data_quality"

    if trade.adx is not None and trade.adx < cfg.min_adx:
        return False, "adx_below_min"

    if cfg.ist_only and trade.is_retest_fill:
        return False, "retest_fill_excluded"

    if not cfg.retest_enabled and trade.is_retest_fill:
        return False, "retest_disabled"

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


def _evaluate(
    trades: list[TradeRecord],
    cfg: FilterConfig,
    *,
    key: str,
    label: str,
    group: str,
    gate_fn=_passes_gates,
) -> VariantResult:
    taken_pnls: list[float] = []
    pnls_by_time: list[tuple[datetime, float]] = []
    skip_reasons: dict[str, int] = {}

    for trade in trades:
        ok, reason = gate_fn(trade, cfg)
        if not ok:
            skip_reasons[reason or "filtered"] = skip_reasons.get(reason or "filtered", 0) + 1
            continue
        taken_pnls.append(trade.pnl)
        ts = trade.closed_at or trade.opened_at
        pnls_by_time.append((ts, trade.pnl))

    n_taken = len(taken_pnls)
    wins = sum(1 for p in taken_pnls if p > 0)
    total = round(sum(taken_pnls), 2)

    return VariantResult(
        key=key,
        label=label,
        group=group,
        filters={
            "min_score": cfg.min_score,
            "short_max_score": cfg.short_max_score,
            "require_strong": cfg.require_strong,
            "min_rr": cfg.min_rr,
            "min_data_quality": cfg.min_data_quality,
            "min_adx": cfg.min_adx,
            "ist_only": cfg.ist_only,
            "retest_enabled": cfg.retest_enabled,
        },
        n_taken=n_taken,
        n_skipped=len(trades) - n_taken,
        total_pnl=total,
        win_rate=round(wins / n_taken, 4) if n_taken else 0.0,
        profit_factor=_profit_factor(taken_pnls),
        max_drawdown=_max_drawdown(pnls_by_time),
        avg_pnl=round(total / n_taken, 2) if n_taken else 0.0,
        skip_reasons=skip_reasons,
    )


def _baseline_config(settings) -> FilterConfig:
    return FilterConfig(
        min_score=float(settings.signal_min_score),
        short_max_score=float(settings.signal_short_max_score),
        require_strong=bool(settings.signal_require_strong),
        min_rr=float(settings.min_risk_reward_ratio),
        min_data_quality=60.0,
        min_adx=float(settings.signal_min_adx),
        ist_only=False,
        retest_enabled=bool(settings.paper_retest_entry_enabled),
    )


def _passes_all_closed(_trade: TradeRecord, _cfg: FilterConfig) -> tuple[bool, str | None]:
    return True, None


def _variant_catalog(baseline: FilterConfig) -> list[tuple[str, str, str, FilterConfig]]:
    variants: list[tuple[str, str, str, FilterConfig]] = [
        (
            "baseline_recorded",
            "Recorded ledger (all closed paper trades)",
            "baseline",
            FilterConfig(),  # unused — special handler takes all closed
        ),
        ("baseline_mtf_v2", "Baseline MTF v2 gates re-applied", "baseline", baseline),
    ]

    for score in (75, 78, 80, 82, 85):
        if score == int(baseline.min_score) and len(variants) == 1:
            continue
        short = _short_max_for(score)
        variants.append(
            (
                f"score_{score}",
                f"Score long≥{score} / short≤{short}",
                "single",
                FilterConfig(
                    min_score=float(score),
                    short_max_score=short,
                    require_strong=baseline.require_strong,
                    min_rr=baseline.min_rr,
                    min_data_quality=baseline.min_data_quality,
                    min_adx=baseline.min_adx,
                    ist_only=baseline.ist_only,
                    retest_enabled=baseline.retest_enabled,
                ),
            )
        )

    for adx in (20, 25, 30, 35):
        if adx == int(baseline.min_adx):
            continue
        variants.append(
            (
                f"adx_{adx}",
                f"ADX ≥ {adx}",
                "single",
                FilterConfig(
                    min_score=baseline.min_score,
                    short_max_score=baseline.short_max_score,
                    require_strong=baseline.require_strong,
                    min_rr=baseline.min_rr,
                    min_data_quality=baseline.min_data_quality,
                    min_adx=float(adx),
                    ist_only=baseline.ist_only,
                    retest_enabled=baseline.retest_enabled,
                ),
            )
        )

    variants.extend(
        [
            (
                "retest_on",
                "Retest enabled (actual mix)",
                "single",
                FilterConfig(
                    min_score=baseline.min_score,
                    short_max_score=baseline.short_max_score,
                    require_strong=baseline.require_strong,
                    min_rr=baseline.min_rr,
                    min_data_quality=baseline.min_data_quality,
                    min_adx=baseline.min_adx,
                    ist_only=False,
                    retest_enabled=True,
                ),
            ),
            (
                "ist_only",
                "IST only (exclude retest fills)",
                "single",
                FilterConfig(
                    min_score=baseline.min_score,
                    short_max_score=baseline.short_max_score,
                    require_strong=baseline.require_strong,
                    min_rr=baseline.min_rr,
                    min_data_quality=baseline.min_data_quality,
                    min_adx=baseline.min_adx,
                    ist_only=True,
                    retest_enabled=True,
                ),
            ),
        ]
    )

    for rr in (2.0, 2.5, 3.0):
        if rr == baseline.min_rr:
            continue
        variants.append(
            (
                f"rr_{str(rr).replace('.', '_')}",
                f"Min R:R ≥ {rr}",
                "single",
                FilterConfig(
                    min_score=baseline.min_score,
                    short_max_score=baseline.short_max_score,
                    require_strong=baseline.require_strong,
                    min_rr=rr,
                    min_data_quality=baseline.min_data_quality,
                    min_adx=baseline.min_adx,
                    ist_only=baseline.ist_only,
                    retest_enabled=baseline.retest_enabled,
                ),
            )
        )

    for dq in (60, 70, 80):
        if dq == int(baseline.min_data_quality):
            continue
        variants.append(
            (
                f"dq_{dq}",
                f"Data quality ≥ {dq}",
                "single",
                FilterConfig(
                    min_score=baseline.min_score,
                    short_max_score=baseline.short_max_score,
                    require_strong=baseline.require_strong,
                    min_rr=baseline.min_rr,
                    min_data_quality=float(dq),
                    min_adx=baseline.min_adx,
                    ist_only=baseline.ist_only,
                    retest_enabled=baseline.retest_enabled,
                ),
            )
        )

    return variants


def _combo_variants(
    singles: list[VariantResult],
    baseline: FilterConfig,
) -> list[tuple[str, str, str, FilterConfig]]:
    """Build combos from top single-axis improvements (PnL, min 5 trades)."""
    eligible = [
        r
        for r in singles
        if r.group == "single"
        and r.n_taken >= 5
        and (r.delta_pnl_vs_baseline or 0) >= 0
    ]
    by_axis: dict[str, VariantResult] = {}
    for r in eligible:
        if r.key.startswith("score_"):
            axis = "score"
        elif r.key.startswith("adx_"):
            axis = "adx"
        elif r.key.startswith("rr_"):
            axis = "rr"
        elif r.key.startswith("dq_"):
            axis = "dq"
        elif r.key in {"ist_only", "retest_on"}:
            axis = r.key
        else:
            continue
        prev = by_axis.get(axis)
        if prev is None or (r.total_pnl, r.profit_factor) > (prev.total_pnl, prev.profit_factor):
            by_axis[axis] = r

    combos: list[tuple[str, str, str, FilterConfig]] = []
    picks = list(by_axis.values())
    if len(picks) >= 2:
        for a, b in itertools.combinations(picks, 2):
            fa = FilterConfig(**a.filters)
            fb = FilterConfig(**b.filters)
            merged = FilterConfig(
                min_score=max(fa.min_score, fb.min_score),
                short_max_score=min(fa.short_max_score, fb.short_max_score),
                require_strong=fa.require_strong or fb.require_strong,
                min_rr=max(fa.min_rr, fb.min_rr),
                min_data_quality=max(fa.min_data_quality, fb.min_data_quality),
                min_adx=max(fa.min_adx, fb.min_adx),
                ist_only=fa.ist_only or fb.ist_only,
                retest_enabled=fa.retest_enabled and fb.retest_enabled,
            )
            key = f"combo_{a.key}_{b.key}"
            label = f"{a.label} + {b.label}"
            combos.append((key, label, "combo", merged))

    if len(picks) >= 3:
        fa, fb, fc = picks[:3]
        filters = [FilterConfig(**x.filters) for x in (fa, fb, fc)]
        merged = FilterConfig(
            min_score=max(f.min_score for f in filters),
            short_max_score=min(f.short_max_score for f in filters),
            require_strong=all(f.require_strong for f in filters),
            min_rr=max(f.min_rr for f in filters),
            min_data_quality=max(f.min_data_quality for f in filters),
            min_adx=max(f.min_adx for f in filters),
            ist_only=any(f.ist_only for f in filters),
            retest_enabled=all(f.retest_enabled for f in filters),
        )
        combos.append(
            (
                "combo_top3",
                f"Top-3 singles merged ({fa.key}, {fb.key}, {fc.key})",
                "combo",
                merged,
            )
        )

    # Hand-picked sensible stacks often requested in ops reviews
    presets = [
        (
            "combo_strict_core",
            "Strict: score≥80 + ADX≥25 + R:R≥2.5",
            FilterConfig(
                min_score=80.0,
                short_max_score=20.0,
                require_strong=True,
                min_rr=2.5,
                min_data_quality=60.0,
                min_adx=25.0,
            ),
        ),
        (
            "combo_quality_trend",
            "Quality: score≥82 + DQ≥70 + ADX≥25",
            FilterConfig(
                min_score=82.0,
                short_max_score=18.0,
                require_strong=True,
                min_rr=2.0,
                min_data_quality=70.0,
                min_adx=25.0,
            ),
        ),
        (
            "combo_ultra",
            "Ultra: score≥85 + ADX≥30 + R:R≥3 + DQ≥70",
            FilterConfig(
                min_score=85.0,
                short_max_score=15.0,
                require_strong=True,
                min_rr=3.0,
                min_data_quality=70.0,
                min_adx=30.0,
            ),
        ),
    ]
    for key, label, cfg in presets:
        combos.append((key, label, "combo", cfg))

    return combos


async def _load_trades(session) -> list[TradeRecord]:
    container = build_container(get_settings())
    account = await container.paper_trading.get_or_create_account(session)
    positions = await PaperRepository(session).list_positions(account.id)

    signal_ids = [p.signal_id for p in positions if p.signal_id]
    signal_map: dict[int, Signal] = {}
    if signal_ids:
        result = await session.execute(select(Signal).where(Signal.id.in_(signal_ids)))
        signal_map = {int(s.id): s for s in result.scalars()}

    trades: list[TradeRecord] = []
    for p in positions:
        sig = signal_map.get(int(p.signal_id)) if p.signal_id else None
        score = (
            float(p.signal_score)
            if p.signal_score is not None
            else (float(sig.score) if sig else None)
        )
        adx: float | None = None
        if p.asset_id is not None:
            row = (
                await session.execute(
                    select(IndicatorSnapshot.adx_14)
                    .where(
                        IndicatorSnapshot.asset_id == p.asset_id,
                        IndicatorSnapshot.timeframe == (p.timeframe or "1h"),
                        IndicatorSnapshot.candle_open_time <= p.opened_at,
                        IndicatorSnapshot.adx_14.is_not(None),
                    )
                    .order_by(IndicatorSnapshot.candle_open_time.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is not None:
                adx = float(row)

        direction = SignalDirection(p.direction)
        trades.append(
            TradeRecord(
                position_id=int(p.id),
                symbol=p.symbol,
                direction=p.direction,
                status=p.status,
                timeframe=p.timeframe or "1h",
                pnl=float(p.realized_pnl),
                exit_reason=p.exit_reason,
                opened_at=ensure_utc(p.opened_at),
                closed_at=ensure_utc(p.closed_at) if p.closed_at else None,
                signal_id=int(p.signal_id) if p.signal_id else None,
                score=score,
                data_quality=float(sig.data_quality) if sig else None,
                risk_reward_ratio=float(sig.risk_reward_ratio)
                if sig and sig.risk_reward_ratio
                else None,
                adx=adx,
                market_phase=sig.market_phase if sig else None,
                is_retest_fill=_is_retest_fill(p.notes),
                is_strong=direction in {
                    SignalDirection.STRONG_LONG,
                    SignalDirection.STRONG_SHORT,
                },
                is_long=direction.is_long,
            )
        )

    await container.aclose()
    trades.sort(key=lambda t: t.opened_at)
    return trades


def _rank_score(r: VariantResult) -> tuple[float, float, float, int]:
    """Higher is better: PF, WR, PnL, then trade count."""
    return (r.profit_factor, r.win_rate, r.total_pnl, r.n_taken)


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    import logging

    for name in ("app", "asyncio", "sqlalchemy"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = False
    logging.basicConfig(level=logging.ERROR, stream=sys.stderr, force=True)

    async with session_scope() as session:
        all_trades = await _load_trades(session)

    closed = [t for t in all_trades if t.status == "closed"]
    print(f"Loaded {len(all_trades)} positions ({len(closed)} closed)", file=sys.stderr)

    baseline_cfg = _baseline_config(settings)
    catalog = _variant_catalog(baseline_cfg)

    results: list[VariantResult] = []
    for key, label, group, cfg in catalog:
        gate_fn = _passes_all_closed if key == "baseline_recorded" else _passes_gates
        results.append(
            _evaluate(closed, cfg, key=key, label=label, group=group, gate_fn=gate_fn)
        )

    baseline = next(r for r in results if r.key == "baseline_recorded")
    for r in results:
        r.delta_pnl_vs_baseline = round(r.total_pnl - baseline.total_pnl, 2)
        r.delta_wr_vs_baseline = round(r.win_rate - baseline.win_rate, 4)
        r.delta_pf_vs_baseline = round(r.profit_factor - baseline.profit_factor, 4)

    singles = [r for r in results if r.group == "single"]
    for key, label, group, cfg in _combo_variants(singles, baseline_cfg):
        vr = _evaluate(closed, cfg, key=key, label=label, group=group)
        vr.delta_pnl_vs_baseline = round(vr.total_pnl - baseline.total_pnl, 2)
        vr.delta_wr_vs_baseline = round(vr.win_rate - baseline.win_rate, 4)
        vr.delta_pf_vs_baseline = round(vr.profit_factor - baseline.profit_factor, 4)
        results.append(vr)

    ranked = sorted(results, key=_rank_score, reverse=True)
    min_trades = 8
    ranked_quality = [
        r
        for r in ranked
        if r.key not in {"baseline_recorded", "baseline_mtf_v2"} and r.n_taken >= min_trades
    ]

    payload: dict[str, Any] = {
        "generated_at": utc_now().isoformat(),
        "method": {
            "type": "paper_filter_counterfactual",
            "description": (
                "Apply alternate entry gates to closed paper trades; "
                "filtered trades contribute $0 (not taken); PnL is recorded outcome"
            ),
            "baseline_gates": asdict(baseline_cfg),
            "swept_axes": {
                "min_score": [75, 78, 80, 82, 85],
                "min_adx": [20, 25, 30, 35],
                "min_rr": [2.0, 2.5, 3.0],
                "min_data_quality": [60, 70, 80],
                "retest": ["retest_on", "ist_only"],
            },
            "caveats": [
                "Small sample (~58 closed trades) — high overfitting risk",
                "Cannot recover trades never taken under looser gates",
                "ADX from nearest indicator snapshot at open — may differ from signal-time gate",
                "Retest counterfactual excludes retest_filled positions for IST-only",
                "PnL is actual paper outcome, not re-simulated exits",
            ],
        },
        "sample": {
            "n_positions": len(all_trades),
            "n_closed": len(closed),
            "n_retest_fills": sum(1 for t in closed if t.is_retest_fill),
            "n_ist": sum(1 for t in closed if not t.is_retest_fill),
            "recorded_pnl_all_closed": round(sum(t.pnl for t in closed), 2),
            "with_adx": sum(1 for t in closed if t.adx is not None),
            "avg_score_long": round(
                sum(t.score for t in closed if t.score and t.is_long)
                / max(1, sum(1 for t in closed if t.score and t.is_long)),
                2,
            ),
            "avg_score_short": round(
                sum(t.score for t in closed if t.score and not t.is_long)
                / max(1, sum(1 for t in closed if t.score and not t.is_long)),
                2,
            ),
            "avg_adx": round(
                sum(t.adx for t in closed if t.adx is not None)
                / max(1, sum(1 for t in closed if t.adx is not None)),
                2,
            ),
            "first_open": min((t.opened_at for t in closed), default=None),
            "last_close": max((t.closed_at for t in closed if t.closed_at), default=None),
        },
        "baseline": asdict(baseline),
        "variants": [asdict(r) for r in results],
        "ranked": [
            {
                "rank": i + 1,
                "key": r.key,
                "label": r.label,
                "group": r.group,
                "n_taken": r.n_taken,
                "total_pnl": r.total_pnl,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
                "max_drawdown": r.max_drawdown,
                "delta_pnl_vs_baseline": r.delta_pnl_vs_baseline,
                "delta_wr_vs_baseline": r.delta_wr_vs_baseline,
                "delta_pf_vs_baseline": r.delta_pf_vs_baseline,
            }
            for i, r in enumerate(ranked)
        ],
        "top_by_pnl": [asdict(r) for r in ranked_quality[:10]],
        "top_by_pf": sorted(
            [asdict(r) for r in ranked_quality if r.n_taken >= min_trades],
            key=lambda x: (x["profit_factor"], x["total_pnl"]),
            reverse=True,
        )[:10],
        "top_by_wr": sorted(
            [asdict(r) for r in ranked_quality if r.n_taken >= min_trades],
            key=lambda x: (x["win_rate"], x["profit_factor"]),
            reverse=True,
        )[:10],
        "recommendations": [],
        "trades_export": [
            {
                "id": t.position_id,
                "symbol": t.symbol,
                "direction": t.direction,
                "pnl": t.pnl,
                "exit_reason": t.exit_reason,
                "score": t.score,
                "adx": t.adx,
                "rr": t.risk_reward_ratio,
                "dq": t.data_quality,
                "retest_fill": t.is_retest_fill,
                "opened_at": t.opened_at.isoformat(),
            }
            for t in closed
        ],
    }

    for k in ("first_open", "last_close"):
        v = payload["sample"][k]
        if isinstance(v, datetime):
            payload["sample"][k] = v.isoformat()

    top3 = ranked_quality[:3]
    if top3:
        payload["recommendations"] = [
            f"Best PnL filter (n≥{min_trades}): {top3[0].label} → PnL {top3[0].total_pnl:+.2f} (Δ{top3[0].delta_pnl_vs_baseline:+.2f})",
            f"WR {top3[0].win_rate:.1%} PF {top3[0].profit_factor:.2f} on {top3[0].n_taken} trades",
        ]

    print(json.dumps(payload, indent=2, default=str))
    print(
        f"BASELINE n={baseline.n_taken} pnl={baseline.total_pnl:+.2f} "
        f"WR={baseline.win_rate:.1%} PF={baseline.profit_factor:.2f} | "
        + " | ".join(
            f"{r.key}={r.total_pnl:+.2f} WR={r.win_rate:.0%} n={r.n_taken}"
            for r in ranked_quality[:5]
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
