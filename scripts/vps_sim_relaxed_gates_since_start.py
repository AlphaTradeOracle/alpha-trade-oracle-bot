"""Counterfactual paper rebuild: live gates vs relaxed Long70 / ADX25 / RSI27.

Runs on isolated sim accounts (default ledger untouched). Since = paper book start.
NO_TRADE rows that fail only on score/ADX/RSI thresholds are revived in-memory
when they would pass the relaxed settings (direction inferred from score band).
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
_LONG_SCORE_RE = re.compile(
    r"(?:Blended )?long score\s+(\d+(?:\.\d+)?)\s+below minimum", re.I
)


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


def _revive_candidate(
    signal: Signal,
    *,
    long_min: float,
    short_min: float,
    short_max: float,
    adx_min: float,
    adx_soft: float,
    rsi_short_min: float,
) -> SignalDirection | None:
    """Return direction if this NO_TRADE would pass relaxed gates; else None."""
    reason = (signal.no_trade_reason or "").strip()
    if not reason:
        return None
    if signal.stop_loss is None or signal.take_profit_1 is None:
        return None
    if signal.take_profit_2 is None or signal.take_profit_3 is None:
        return None
    if float(signal.data_quality or 0) < 60.0:
        return None
    rr = float(signal.risk_reward_ratio or 0.0)
    if rr < 2.0:
        return None

    score = float(signal.score)
    direction = _infer_direction(
        score, long_min=long_min, short_min=short_min, short_max=short_max
    )
    if direction is None:
        return None

    # Hard rejects we do not relax
    blocked_other = (
        "Data quality",
        "Risk/reward",
        "Volatility too high",
        "regime",
        "Regime",
        "No reliable risk",
        "exhaustion band",
        "above maximum",
        "overbought",
    )
    if any(tok in reason for tok in blocked_other):
        # Allow long score-below-minimum through (that is what we relax).
        if "below minimum" in reason.lower() and direction.is_long:
            pass
        elif "oversold" in reason.lower() and direction.is_short:
            pass
        elif "ADX" in reason or "Trend strength" in reason or "Range market" in reason:
            pass
        else:
            return None

    high_conv = (
        (direction.is_long and score >= long_min)
        or (direction.is_short and score <= short_max)
    )
    adx_floor = adx_soft if high_conv else adx_min
    adx = _parse_adx(reason)
    if adx is not None and adx < adx_floor:
        return None
    # Reason mentions ADX failure but we couldn't parse — keep conservative skip
    if adx is None and ("ADX" in reason or "Trend strength too low" in reason):
        return None

    if direction.is_short:
        rsi = _parse_rsi(reason)
        if "oversold" in reason.lower() or "short minimum" in reason.lower():
            if rsi is None:
                return None
            if rsi < rsi_short_min:
                return None

    if direction.is_long:
        m = _LONG_SCORE_RE.search(reason)
        if m and float(m.group(1)) < long_min:
            return None
        if "below minimum" in reason.lower() and score < long_min:
            return None

    return direction


async def _live_snapshot(session) -> dict:
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
        direction = _revive_candidate(
            signal,
            long_min=long_min,
            short_min=short_min,
            short_max=short_max,
            adx_min=adx_min,
            adx_soft=adx_soft,
            rsi_short_min=rsi_short_min,
        )
        if direction is None:
            continue
        # In-memory only — never flush these mutations.
        signal.direction = direction.value
        out.append(signal)
    return out


async def _run_rebuild(
    paper,
    provider,
    *,
    acct_name: str,
    since: datetime,
    long_min: float,
    short_max: float,
    short_min: float,
    adx_min: float,
    rsi_short_min: float,
    revive: bool,
) -> dict[str, Any]:
    original_goa = paper.get_or_create_account
    orig_list_since = SignalRepository.list_since
    orig_long = float(paper._settings.signal_min_score)
    orig_short_max = float(paper._settings.signal_short_max_score)
    orig_short_min = float(paper._settings.signal_short_min_score)
    orig_adx = float(paper._settings.signal_min_adx)
    orig_rsi = float(paper._settings.signal_rsi_short_min)

    paper._settings.signal_min_score = float(long_min)
    paper._settings.signal_short_max_score = float(short_max)
    paper._settings.signal_short_min_score = float(short_min)
    paper._settings.signal_min_adx = float(adx_min)
    paper._settings.signal_rsi_short_min = float(rsi_short_min)
    adx_soft = float(paper._settings.signal_min_adx_soft)

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
        )
        revived_n = len(extra)
        seen = {s.id for s in base}
        merged = list(base) + [s for s in extra if s.id not in seen]
        merged.sort(key=lambda s: ensure_utc(s.created_at))
        return merged

    async with session_scope() as session:
        repo = PaperRepository(session)
        account = await repo.get_or_create_account(
            name=acct_name,
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
                trades.append(
                    {
                        "id": p.id,
                        "symbol": p.symbol,
                        "direction": p.direction,
                        "status": p.status,
                        "score": float(p.signal_score) if p.signal_score is not None else None,
                        "signal_id": p.signal_id,
                        "entry": float(p.entry_price) if p.entry_price is not None else None,
                        "pnl": float(p.realized_pnl or 0),
                        "exit_reason": p.exit_reason,
                        "opened_at": ensure_utc(p.opened_at).isoformat() if p.opened_at else None,
                        "closed_at": ensure_utc(p.closed_at).isoformat() if p.closed_at else None,
                    }
                )
            filled = [t for t in trades if t["status"] in {"open", "closed"}]
            out = {
                "key": acct_name,
                "label": (
                    f"L>={long_min:g} ADX>={adx_min:g} RSI_short>={rsi_short_min:g}"
                    + (" +revive" if revive else "")
                ),
                "long_min": long_min,
                "adx_min": adx_min,
                "rsi_short_min": rsi_short_min,
                "revive": revive,
                "revived_candidates": revived_n,
                "opened": result.backfill.opened if result.backfill else 0,
                "retest_filled": result.retest_filled,
                "retest_skipped": result.retest_skipped,
                "replayed": result.replayed,
                "still_open": result.still_open,
                "equity": float(summary.equity),
                "cash": float(summary.cash_balance),
                "realized_pnl": float(summary.realized_pnl),
                "closed": int(summary.closed_trades),
                "open": int(summary.open_positions),
                "pending": int(summary.pending_positions),
                "win_rate": float(summary.win_rate),
                "profit_factor": float(summary.profit_factor),
                "total_r": float(summary.total_r),
                "expectancy_r": float(summary.expectancy_r),
                "long_n": sum(1 for t in filled if SignalDirection(t["direction"]).is_long),
                "short_n": sum(1 for t in filled if SignalDirection(t["direction"]).is_short),
                "trades": trades,
            }
        finally:
            paper.get_or_create_account = original_goa  # type: ignore[method-assign]
            SignalRepository.list_since = orig_list_since  # type: ignore[method-assign]
            paper._settings.signal_min_score = orig_long
            paper._settings.signal_short_max_score = orig_short_max
            paper._settings.signal_short_min_score = orig_short_min
            paper._settings.signal_min_adx = orig_adx
            paper._settings.signal_rsi_short_min = orig_rsi
            await repo.reset_ledger(account)

    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-08-05T00:00:00+00:00")
    parser.add_argument("--out", default="/tmp/sim_relaxed_gates.json")
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    since = ensure_utc(datetime.fromisoformat(args.since))
    container = build_container()
    paper = container.paper_trading
    provider = container.paper_price_provider

    try:
        async with session_scope() as session:
            live = await _live_snapshot(session)

        print(f"since={since.isoformat()} live={live}", flush=True)

        print("sim BASELINE L75 ADX30 RSI33 ...", flush=True)
        base = await _run_rebuild(
            paper,
            provider,
            acct_name="sim_gate_baseline_aug5",
            since=since,
            long_min=75.0,
            short_max=30.0,
            short_min=18.0,
            adx_min=30.0,
            rsi_short_min=33.0,
            revive=False,
        )
        print(
            f"  equity=${base['equity']:.2f} closed={base['closed']} open={base['open']} "
            f"pending={base['pending']} filled={base['retest_filled']} "
            f"WR={base['win_rate']:.1%} PF={base['profit_factor']:.2f}",
            flush=True,
        )

        # Only the two gates the user asked to re-check (long min stays 75).
        print("sim ADX25 RSI27 (L75) +revive ...", flush=True)
        relaxed = await _run_rebuild(
            paper,
            provider,
            acct_name="sim_gate_adx25_rsi27_aug5",
            since=since,
            long_min=75.0,
            short_max=30.0,
            short_min=18.0,
            adx_min=25.0,
            rsi_short_min=27.0,
            revive=True,
        )
        print(
            f"  equity=${relaxed['equity']:.2f} closed={relaxed['closed']} open={relaxed['open']} "
            f"pending={relaxed['pending']} filled={relaxed['retest_filled']} "
            f"revived={relaxed['revived_candidates']} "
            f"WR={relaxed['win_rate']:.1%} PF={relaxed['profit_factor']:.2f}",
            flush=True,
        )

        async with session_scope() as session:
            live_after = await _live_snapshot(session)

        base_ids = {
            t["signal_id"]
            for t in base["trades"]
            if t["status"] in {"open", "closed", "pending"} and t["signal_id"]
        }
        incremental = [
            t
            for t in relaxed["trades"]
            if t["status"] in {"open", "closed", "pending"}
            and t["signal_id"] not in base_ids
        ]

        out = {
            "generated_at": utc_now().isoformat(),
            "since": since.isoformat(),
            "test": {
                "SIGNAL_MIN_ADX": 25,
                "SIGNAL_RSI_SHORT_MIN": 27,
                "SIGNAL_MIN_SCORE": 75,
            },
            "live_now": {
                "equity_proxy_cash": live["cash_balance"],
                "realized_pnl": live["realized_pnl"],
                "closed": live["closed_n"],
                "open": live["open_n"],
                "pending": live["pending_n"],
                "settings": {
                    "long_min": 75,
                    "adx_min": 30,
                    "rsi_short_min": 33,
                    "short_band": [18, 30],
                },
            },
            "live_after_safety": live_after,
            "live_untouched": (
                live_after["closed_n"] == live["closed_n"]
                and live_after["open_n"] == live["open_n"]
                and abs(float(live_after["cash_balance"]) - float(live["cash_balance"])) < 0.01
            ),
            "baseline": {k: v for k, v in base.items() if k != "trades"},
            "relaxed": {k: v for k, v in relaxed.items() if k != "trades"},
            "delta_relaxed_minus_baseline": {
                "equity": round(relaxed["equity"] - base["equity"], 2),
                "realized_pnl": round(relaxed["realized_pnl"] - base["realized_pnl"], 2),
                "closed": relaxed["closed"] - base["closed"],
                "open": relaxed["open"] - base["open"],
                "pending": relaxed["pending"] - base["pending"],
                "retest_filled": relaxed["retest_filled"] - base["retest_filled"],
                "pf": round(relaxed["profit_factor"] - base["profit_factor"], 3),
                "incremental_n": len(incremental),
            },
            "baseline_trades": [
                t for t in base["trades"] if t["status"] in {"open", "closed", "pending"}
            ],
            "relaxed_trades": [
                t for t in relaxed["trades"] if t["status"] in {"open", "closed", "pending"}
            ],
            "incremental_trades": incremental,
        }
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"WROTE {args.out}", flush=True)
        print(
            json.dumps(
                {
                    "live_equity": live["cash_balance"],
                    "baseline_equity": round(base["equity"], 2),
                    "adx25_rsi27_equity": round(relaxed["equity"], 2),
                    "delta": out["delta_relaxed_minus_baseline"],
                    "revived_candidates": relaxed["revived_candidates"],
                    "live_untouched": out["live_untouched"],
                },
                indent=2,
            ),
            flush=True,
        )
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
