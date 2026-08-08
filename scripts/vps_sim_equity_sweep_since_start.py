"""Equity sweep since paper start: which gate/retest settings beat live.

Isolated sim accounts only — default ledger untouched.
Revives stored NO_TRADE rows that would pass the variant's gates (score/ADX/RSI),
so the sim is not limited to already-actionable DB directions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from app.container import build_container
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.models.signal import Signal
from app.repositories.paper_repository import PaperRepository
from app.repositories.signal_repository import SignalRepository

_ADX_RE = re.compile(r"ADX\s+(\d+(?:\.\d+)?)", re.I)
_RSI_RE = re.compile(r"RSI\s+(\d+(?:\.\d+)?)", re.I)


def _parse_adx(reason: str | None) -> float | None:
    if not reason:
        return None
    m = _ADX_RE.search(reason)
    return float(m.group(1)) if m else None


def _parse_rsi(reason: str | None) -> float | None:
    if not reason:
        return None
    m = _RSI_RE.search(reason)
    return float(m.group(1)) if m else None


def _infer_direction(
    score: float, *, long_min: float, short_min: float, short_max: float
) -> SignalDirection | None:
    if score >= long_min:
        return SignalDirection.STRONG_LONG
    if short_min < score <= short_max:
        return SignalDirection.STRONG_SHORT
    return None


def _revive_ok(
    signal: Signal,
    *,
    long_min: float,
    short_min: float,
    short_max: float,
    adx_min: float,
    adx_soft: float,
    rsi_short_min: float,
    rr_min: float,
) -> SignalDirection | None:
    reason = (signal.no_trade_reason or "").strip()
    if not reason:
        return None
    if signal.stop_loss is None or signal.take_profit_1 is None:
        return None
    if signal.take_profit_2 is None or signal.take_profit_3 is None:
        return None
    if float(signal.data_quality or 0) < 60.0 and "Data quality" in reason:
        # Keep hard DQ blocks (true 0 / missing primary). Historical 59.99
        # was the false HTF-cap — allow revive when score/RR otherwise ok.
        if abs(float(signal.data_quality or 0) - 59.99) > 0.001:
            return None
    rr = float(signal.risk_reward_ratio or 0.0)
    if rr < rr_min:
        return None

    score = float(signal.score)
    direction = _infer_direction(
        score, long_min=long_min, short_min=short_min, short_max=short_max
    )
    if direction is None:
        return None

    # Hard rejects we never revive
    hard = (
        "Volatility too high",
        "No reliable risk",
        "exhaustion band",
        "regime",
        "Regime",
    )
    if any(tok in reason for tok in hard):
        return None
    if direction.is_long and "overbought" in reason.lower() and "RSI" in reason:
        # Only block if RSI would still fail long max — parsed below loosely
        pass
    if direction.is_short and "above maximum" in reason.lower() and "short" in reason.lower():
        # short score above max — direction inference already failed for band
        pass

    high_conv = (direction.is_long and score >= long_min) or (
        direction.is_short and score <= short_max
    )
    adx_floor = adx_soft if high_conv else adx_min
    adx = _parse_adx(reason)
    rsi = _parse_rsi(reason)

    if "ADX" in reason or "Trend strength" in reason or "Range market" in reason:
        if adx is not None and adx < adx_floor:
            return None
    if direction.is_short and "oversold" in reason.lower():
        if rsi is not None and rsi < rsi_short_min:
            return None
    if direction.is_long and "overbought" in reason.lower():
        # keep live long RSI max 75 — if reason says overbought and RSI>75, skip
        if rsi is not None and rsi > 75.0:
            return None

    # Score-below-minimum / data-quality-59.99 / ADX soft-pass → revive
    return direction


async def _load_revivable(
    session,
    *,
    since: datetime,
    long_min: float,
    short_min: float,
    short_max: float,
    adx_min: float,
    adx_soft: float,
    rsi_short_min: float,
    rr_min: float,
) -> list[Signal]:
    result = await session.execute(
        select(Signal)
        .where(
            Signal.created_at >= since,
            Signal.direction == SignalDirection.NO_TRADE.value,
            Signal.stop_loss.is_not(None),
            Signal.take_profit_1.is_not(None),
        )
        .order_by(Signal.created_at.asc())
        .limit(50_000)
    )
    out: list[Signal] = []
    for signal in result.scalars():
        direction = _revive_ok(
            signal,
            long_min=long_min,
            short_min=short_min,
            short_max=short_max,
            adx_min=adx_min,
            adx_soft=adx_soft,
            rsi_short_min=rsi_short_min,
            rr_min=rr_min,
        )
        if direction is None:
            continue
        signal.direction = direction.value
        out.append(signal)
    return out


async def _live_snapshot(session) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                select cash_balance, realized_pnl, initial_balance,
                  (select count(*) from paper_positions p
                   where p.account_id=a.id and p.status='closed') as closed_n,
                  (select count(*) from paper_positions p
                   where p.account_id=a.id and p.status='open') as open_n,
                  (select count(*) from paper_positions p
                   where p.account_id=a.id and p.status='pending') as pending_n
                from paper_accounts a where name='default'
                """
            )
        )
    ).mappings().one()
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in dict(row).items()}


async def _run_variant(
    paper,
    provider,
    *,
    key: str,
    label: str,
    since: datetime,
    long_min: float,
    short_max: float,
    short_min: float,
    adx_min: float,
    adx_soft: float,
    rsi_short_min: float,
    rr_min: float,
    zone_near: float,
    pending_mult: int,
    retest_enabled: bool,
    revive: bool,
) -> dict[str, Any]:
    original_goa = paper.get_or_create_account
    orig_list_since = SignalRepository.list_since

    # Snapshot settings we mutate
    snap = {
        "signal_min_score": float(paper._settings.signal_min_score),
        "signal_short_max_score": float(paper._settings.signal_short_max_score),
        "signal_short_min_score": float(paper._settings.signal_short_min_score),
        "signal_min_adx": float(paper._settings.signal_min_adx),
        "signal_min_adx_soft": float(paper._settings.signal_min_adx_soft),
        "signal_rsi_short_min": float(paper._settings.signal_rsi_short_min),
        "min_risk_reward_ratio": float(paper._settings.min_risk_reward_ratio),
        "paper_retest_zone_near": float(paper._settings.paper_retest_zone_near),
        "paper_retest_pending_multiplier": int(paper._settings.paper_retest_pending_multiplier),
        "paper_retest_entry_enabled": bool(paper._settings.paper_retest_entry_enabled),
    }

    paper._settings.signal_min_score = float(long_min)
    paper._settings.signal_short_max_score = float(short_max)
    paper._settings.signal_short_min_score = float(short_min)
    paper._settings.signal_min_adx = float(adx_min)
    paper._settings.signal_min_adx_soft = float(adx_soft)
    paper._settings.signal_rsi_short_min = float(rsi_short_min)
    paper._settings.min_risk_reward_ratio = float(rr_min)
    paper._settings.paper_retest_zone_near = float(zone_near)
    paper._settings.paper_retest_pending_multiplier = int(pending_mult)
    paper._settings.paper_retest_entry_enabled = bool(retest_enabled)

    revived_n = 0

    async def _list_since(self, since_dt, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal revived_n
        base = await orig_list_since(self, since_dt, **kwargs)
        if not revive:
            return base
        extra = await _load_revivable(
            self._session,
            since=ensure_utc(since_dt),
            long_min=long_min,
            short_min=short_min,
            short_max=short_max,
            adx_min=adx_min,
            adx_soft=adx_soft,
            rsi_short_min=rsi_short_min,
            rr_min=rr_min,
        )
        revived_n = len(extra)
        seen = {s.id for s in base}
        # Detach revived rows so session_scope commit cannot pollute live signals.
        merged_extra = []
        for s in extra:
            if s.id in seen:
                continue
            self._session.expunge(s)
            merged_extra.append(s)
        merged = list(base) + merged_extra
        merged.sort(key=lambda s: ensure_utc(s.created_at))
        return merged

    async with session_scope() as session:
        repo = PaperRepository(session)
        account = await repo.get_or_create_account(
            name=f"sim_sweep_{key}"[:64],
            initial_balance=Decimal(str(paper._settings.paper_initial_balance)),
            margin_per_trade=Decimal(str(paper._settings.paper_margin_per_trade)),
            leverage=float(paper._settings.paper_leverage),
        )

        async def _goa(_session, *args, **kwargs):
            return account

        paper.get_or_create_account = _goa  # type: ignore[method-assign]
        SignalRepository.list_since = _list_since  # type: ignore[method-assign]
        try:
            with paper._without_notifications():
                result = await paper.rebuild_from_signals(
                    session,
                    since=since,
                    provider=provider,
                    providers=None,
                    dispatched_only=False,
                    one_per_symbol=False,
                    symbols=None,
                )
                summary = await paper.summary(session)
                positions = await repo.list_positions(account.id)

            trades = []
            for p in sorted(
                positions,
                key=lambda x: ensure_utc(x.opened_at) if x.opened_at else utc_now(),
            ):
                if p.status not in {"open", "closed", "pending", "cancelled"}:
                    continue
                trades.append(
                    {
                        "symbol": p.symbol,
                        "direction": p.direction,
                        "status": p.status,
                        "score": float(p.signal_score) if p.signal_score is not None else None,
                        "signal_id": p.signal_id,
                        "pnl": float(p.realized_pnl or 0),
                        "exit_reason": p.exit_reason,
                        "opened_at": ensure_utc(p.opened_at).isoformat() if p.opened_at else None,
                    }
                )
            filled = [t for t in trades if t["status"] in {"open", "closed"}]
            closed = [t for t in trades if t["status"] == "closed"]
            out = {
                "key": key,
                "label": label,
                "settings": {
                    "long_min": long_min,
                    "short_band": [short_min, short_max],
                    "adx_min": adx_min,
                    "adx_soft": adx_soft,
                    "rsi_short_min": rsi_short_min,
                    "rr_min": rr_min,
                    "zone_near": zone_near,
                    "pending_mult": pending_mult,
                    "retest_enabled": retest_enabled,
                    "revive": revive,
                },
                "revived_candidates": revived_n,
                "retest_filled": result.retest_filled,
                "retest_skipped": result.retest_skipped,
                "equity": float(summary.equity),
                "cash": float(summary.cash_balance),
                "realized_pnl": float(summary.realized_pnl),
                "closed": int(summary.closed_trades),
                "open": int(summary.open_positions),
                "pending": int(summary.pending_positions),
                "cancelled": sum(1 for t in trades if t["status"] == "cancelled"),
                "win_rate": float(summary.win_rate),
                "profit_factor": float(summary.profit_factor),
                "total_r": float(summary.total_r),
                "expectancy_r": float(summary.expectancy_r),
                "long_n": sum(1 for t in filled if SignalDirection(t["direction"]).is_long),
                "short_n": sum(1 for t in filled if SignalDirection(t["direction"]).is_short),
                "closed_pnl_sum": round(sum(t["pnl"] for t in closed), 2),
                "top_trades": sorted(closed, key=lambda t: t["pnl"], reverse=True)[:5],
                "worst_trades": sorted(closed, key=lambda t: t["pnl"])[:5],
                "filled_symbols": sorted({t["symbol"] for t in filled}),
            }
        finally:
            paper.get_or_create_account = original_goa  # type: ignore[method-assign]
            SignalRepository.list_since = orig_list_since  # type: ignore[method-assign]
            for k, v in snap.items():
                setattr(paper._settings, k, v)
            await repo.reset_ledger(account)

    return out


def _catalog() -> list[dict[str, Any]]:
    """Baseline first; then single-axis and combo experiments."""
    base = dict(
        long_min=75.0,
        short_min=18.0,
        short_max=30.0,
        adx_min=30.0,
        adx_soft=20.0,
        rsi_short_min=33.0,
        rr_min=2.0,
        zone_near=0.55,
        pending_mult=6,
        retest_enabled=True,
        revive=False,
    )
    variants: list[dict[str, Any]] = [
        {
            "key": "baseline",
            "label": "Live baseline L75 / ADX30 / RSI33 / 0.55×6",
            **base,
        },
        {
            "key": "baseline_revive_dq59",
            "label": "Baseline + revive DQ59.99 / soft-gate NO_TRADEs",
            **{**base, "revive": True},
        },
        {
            "key": "L70_revive",
            "label": "Long≥70 + revive",
            **{**base, "long_min": 70.0, "revive": True},
        },
        {
            "key": "L72_revive",
            "label": "Long≥72 + revive",
            **{**base, "long_min": 72.0, "revive": True},
        },
        {
            "key": "short_max32",
            "label": "Short max 32 + revive",
            **{**base, "short_max": 32.0, "revive": True},
        },
        {
            "key": "rsi30",
            "label": "RSI short≥30 + revive",
            **{**base, "rsi_short_min": 30.0, "revive": True},
        },
        {
            "key": "rsi27",
            "label": "RSI short≥27 + revive",
            **{**base, "rsi_short_min": 27.0, "revive": True},
        },
        {
            "key": "adx25",
            "label": "ADX hard≥25 + revive",
            **{**base, "adx_min": 25.0, "revive": True},
        },
        {
            "key": "adx25_rsi27",
            "label": "ADX25 + RSI27 + revive",
            **{**base, "adx_min": 25.0, "rsi_short_min": 27.0, "revive": True},
        },
        {
            "key": "retest_045x8",
            "label": "Retest zone 0.45 · pending×8",
            **{**base, "zone_near": 0.45, "pending_mult": 8},
        },
        {
            "key": "retest_x8",
            "label": "Retest pending×8 only",
            **{**base, "pending_mult": 8},
        },
        {
            "key": "retest_off",
            "label": "Retest OFF (chase entry)",
            **{**base, "retest_enabled": False, "revive": True},
        },
        {
            "key": "combo_active",
            "label": "L72 + RSI30 + ADX25 + 0.55×8 + revive",
            **{
                **base,
                "long_min": 72.0,
                "rsi_short_min": 30.0,
                "adx_min": 25.0,
                "pending_mult": 8,
                "revive": True,
            },
        },
        {
            "key": "combo_loose",
            "label": "L70 + RSI27 + ADX25 + short32 + revive",
            **{
                **base,
                "long_min": 70.0,
                "rsi_short_min": 27.0,
                "adx_min": 25.0,
                "short_max": 32.0,
                "revive": True,
            },
        },
    ]
    return variants


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-08-05T00:00:00+00:00")
    parser.add_argument("--out", default="/tmp/sim_equity_sweep.json")
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated variant keys (default: all)",
    )
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    since = ensure_utc(datetime.fromisoformat(args.since))
    container = build_container()
    paper = container.paper_trading
    provider = container.paper_price_provider

    catalog = _catalog()
    if args.only.strip():
        want = {k.strip() for k in args.only.split(",") if k.strip()}
        catalog = [v for v in catalog if v["key"] in want]

    try:
        async with session_scope() as session:
            live = await _live_snapshot(session)

        print(
            f"since={since.isoformat()} live_cash=${live['cash_balance']:.2f} "
            f"closed={live['closed_n']} variants={len(catalog)}",
            flush=True,
        )

        results: list[dict[str, Any]] = []
        for v in catalog:
            cfg = dict(v)
            key = str(cfg.pop("key"))
            label = str(cfg.pop("label"))
            print(f"sim {key} …", flush=True)
            row = await _run_variant(
                paper,
                provider,
                key=key,
                label=label,
                since=since,
                **cfg,
            )
            results.append(row)
            print(
                f"  equity=${row['equity']:.2f} realized=${row['realized_pnl']:.2f} "
                f"closed={row['closed']} open={row['open']} pending={row['pending']} "
                f"filled={row['retest_filled']} skip={row['retest_skipped']} "
                f"WR={row['win_rate']:.0%} PF={row['profit_factor']:.2f} "
                f"revive={row['revived_candidates']}",
                flush=True,
            )

        async with session_scope() as session:
            live_after = await _live_snapshot(session)

        baseline = next(r for r in results if r["key"] == "baseline")
        ranked = sorted(results, key=lambda r: r["equity"], reverse=True)
        for r in results:
            r["delta_vs_baseline_equity"] = round(r["equity"] - baseline["equity"], 2)
            r["delta_vs_baseline_realized"] = round(
                r["realized_pnl"] - baseline["realized_pnl"], 2
            )

        out = {
            "generated_at": utc_now().isoformat(),
            "since": since.isoformat(),
            "live_now": live,
            "live_after_safety": live_after,
            "live_untouched": (
                live_after["closed_n"] == live["closed_n"]
                and live_after["open_n"] == live["open_n"]
                and abs(float(live_after["cash_balance"]) - float(live["cash_balance"])) < 0.01
            ),
            "baseline_key": "baseline",
            "ranked": [
                {
                    "rank": i + 1,
                    "key": r["key"],
                    "label": r["label"],
                    "equity": round(r["equity"], 2),
                    "delta_equity": r["delta_vs_baseline_equity"],
                    "realized_pnl": round(r["realized_pnl"], 2),
                    "closed": r["closed"],
                    "open": r["open"],
                    "pending": r["pending"],
                    "retest_filled": r["retest_filled"],
                    "wr": round(r["win_rate"] * 100, 1),
                    "pf": round(r["profit_factor"], 3),
                    "settings": r["settings"],
                    "filled_symbols": r["filled_symbols"],
                    "revived_candidates": r["revived_candidates"],
                }
                for i, r in enumerate(ranked)
            ],
            "variants": results,
            "recommendation": {
                "best_key": ranked[0]["key"],
                "best_label": ranked[0]["label"],
                "best_equity": round(ranked[0]["equity"], 2),
                "best_settings": ranked[0]["settings"],
                "beats_baseline": ranked[0]["equity"] > baseline["equity"] + 0.5,
            },
        }
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"WROTE {args.out}", flush=True)
        print(
            json.dumps(
                {
                    "live_untouched": out["live_untouched"],
                    "top3": out["ranked"][:3],
                    "baseline_equity": round(baseline["equity"], 2),
                    "recommendation": out["recommendation"],
                },
                indent=2,
            ),
            flush=True,
        )
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
