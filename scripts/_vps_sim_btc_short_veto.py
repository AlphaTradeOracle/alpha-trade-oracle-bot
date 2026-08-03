"""Sim: BTC 1h+4h momentum veto for new SHORT paper entries (Scenario B window).

Never touches account ``default``. Compares baseline rebuild vs veto rebuild.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import select, text

from app.container import build_container
from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc
from app.database.session import session_scope
from app.models.market import Asset
from app.models.signal import Signal
from app.repositories.paper_repository import PaperRepository

SINCE = datetime(2026, 7, 31, 16, 32, 35, tzinfo=timezone.utc)
OUT = Path("/tmp/sim_btc_short_veto.json")
ACCT_BASE = "sim_btc_veto_off"
ACCT_VETO = "sim_btc_veto_on"


@dataclass(frozen=True)
class VetoConfig:
    name: str
    # Last completed candle return thresholds (percent).
    ret_1h_pct: float
    ret_4h_pct: float
    # Require BOTH timeframes above threshold.
    require_both: bool = True


VARIANTS = [
    VetoConfig("mild_0.3_0.6", 0.3, 0.6),
    VetoConfig("mid_0.5_1.0", 0.5, 1.0),
    VetoConfig("strict_0.8_1.5", 0.8, 1.5),
]


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


async def _load_btc_frames() -> dict[str, pd.DataFrame]:
    async with session_scope() as session:
        out: dict[str, pd.DataFrame] = {}
        for tf in ("1h", "4h"):
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT c.open_time, c.open, c.high, c.low, c.close, c.volume
                        FROM market_candles c
                        JOIN assets a ON a.id = c.asset_id
                        WHERE a.symbol = 'BTCUSDT' AND c.timeframe = :tf
                          AND c.open_time >= :start
                          AND c.open_time <= :end
                        ORDER BY c.open_time
                        """
                    ),
                    {
                        "tf": tf,
                        "start": datetime(2026, 7, 20, tzinfo=timezone.utc),
                        "end": datetime(2026, 8, 5, tzinfo=timezone.utc),
                    },
                )
            ).mappings().all()
            if not rows:
                out[tf] = pd.DataFrame()
                continue
            idx = pd.to_datetime([r["open_time"] for r in rows], utc=True)
            out[tf] = pd.DataFrame(
                {
                    "open": [float(r["open"]) for r in rows],
                    "high": [float(r["high"]) for r in rows],
                    "low": [float(r["low"]) for r in rows],
                    "close": [float(r["close"]) for r in rows],
                    "volume": [float(r["volume"]) for r in rows],
                },
                index=idx,
            )
        return out


def _ret_completed(df: pd.DataFrame, asof: datetime, hours: int) -> float | None:
    if df is None or df.empty:
        return None
    asof = ensure_utc(asof)
    # Completed candles: open_time + duration <= asof
    duration = pd.Timedelta(hours=hours)
    idx = df.index
    mask = (idx + duration) <= pd.Timestamp(asof)
    hist = df.loc[mask]
    if len(hist) < 1:
        return None
    row = hist.iloc[-1]
    o, c = float(row["open"]), float(row["close"])
    if o <= 0:
        return None
    return (c / o - 1.0) * 100.0


def _veto_short(
    asof: datetime,
    frames: dict[str, pd.DataFrame],
    cfg: VetoConfig,
) -> tuple[bool, dict]:
    r1 = _ret_completed(frames.get("1h", pd.DataFrame()), asof, 1)
    r4 = _ret_completed(frames.get("4h", pd.DataFrame()), asof, 4)
    detail = {"ret_1h": r1, "ret_4h": r4, "cfg": cfg.name}
    if r1 is None or r4 is None:
        return False, {**detail, "reason": "btc_data_missing"}
    up_1 = r1 >= cfg.ret_1h_pct
    up_4 = r4 >= cfg.ret_4h_pct
    if cfg.require_both:
        hit = up_1 and up_4
    else:
        hit = up_1 or up_4
    return hit, {
        **detail,
        "up_1h": up_1,
        "up_4h": up_4,
        "veto": hit,
    }


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
        "closed": int(summary.closed_trades),
        "win_rate": round(float(summary.win_rate) * 100, 1),
        "profit_factor": round(float(summary.profit_factor), 3),
        "realized": round(float(summary.realized_pnl), 2),
    }


async def _run_sim(
    paper,
    provider,
    *,
    name: str,
    symbols: set[str],
    frames: dict[str, pd.DataFrame],
    veto: VetoConfig | None,
) -> dict:
    orig_goa = paper.get_or_create_account
    orig_gates = paper._passes_paper_gates
    veto_stats = {"checked_shorts": 0, "vetoed": 0, "allowed": 0}

    def _gates(signal: Signal) -> bool:
        if not orig_gates(signal):
            return False
        if veto is None:
            return True
        try:
            direction = SignalDirection(signal.direction)
        except ValueError:
            return False
        if not direction.is_short:
            return True
        veto_stats["checked_shorts"] += 1
        hit, _ = _veto_short(ensure_utc(signal.created_at), frames, veto)
        if hit:
            veto_stats["vetoed"] += 1
            return False
        veto_stats["allowed"] += 1
        return True

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
        paper._passes_paper_gates = _gates  # type: ignore[method-assign]
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
            paper._passes_paper_gates = orig_gates  # type: ignore[method-assign]

    return {
        "name": name,
        "veto": None if veto is None else veto.name,
        "thresholds": None
        if veto is None
        else {"ret_1h_pct": veto.ret_1h_pct, "ret_4h_pct": veto.ret_4h_pct},
        "equity": round(float(summary.equity), 2),
        "realized": round(float(summary.realized_pnl), 2),
        "closed": int(summary.closed_trades),
        "open": int(summary.open_positions),
        "pending": int(summary.pending_positions),
        "win_rate": round(float(summary.win_rate) * 100, 1),
        "profit_factor": round(float(summary.profit_factor), 3),
        "retest_filled": int(result.retest_filled),
        "retest_skipped": int(result.retest_skipped),
        "veto_stats": veto_stats,
    }


async def _counterfactual_on_desk_trades(
    frames: dict[str, pd.DataFrame], cfg: VetoConfig
) -> dict:
    """Fast filter: drop closed shorts that would have been vetoed at open."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT p.symbol, p.direction, p.signal_score, p.opened_at,
                           p.closed_at, p.exit_reason, p.realized_pnl::float AS pnl
                    FROM paper_positions p
                    JOIN paper_accounts a ON a.id = p.account_id
                    WHERE a.name = 'default' AND p.status = 'closed'
                      AND p.opened_at >= :since
                    ORDER BY p.opened_at
                    """
                ),
                {"since": SINCE},
            )
        ).mappings().all()

    kept = []
    removed = []
    for r in rows:
        if r["direction"] != "SHORT":
            kept.append(dict(r))
            continue
        hit, detail = _veto_short(ensure_utc(r["opened_at"]), frames, cfg)
        item = {**dict(r), "opened_at": r["opened_at"].isoformat(), **detail}
        if hit:
            removed.append(item)
        else:
            kept.append(item)

    def kpi(trades: list[dict]) -> dict:
        closed = trades
        wins = [t for t in closed if float(t["pnl"]) > 0]
        losses = [t for t in closed if float(t["pnl"]) < 0]
        gp = sum(float(t["pnl"]) for t in wins)
        gl = sum(float(t["pnl"]) for t in losses)
        realized = sum(float(t["pnl"]) for t in closed)
        pf = (gp / abs(gl)) if gl < 0 else (0.0 if gp == 0 else 99.0)
        return {
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
            "realized": round(realized, 2),
            "equity": round(5000.0 + realized, 2),
            "profit_factor": round(pf, 3),
            "removed": len(removed),
            "removed_pnl": round(sum(float(t["pnl"]) for t in removed), 2),
        }

    baseline_rows = [
        {
            **dict(r),
            "opened_at": r["opened_at"].isoformat(),
            "pnl": float(r["pnl"]),
        }
        for r in rows
    ]
    return {
        "cfg": cfg.name,
        "thresholds": {"ret_1h_pct": cfg.ret_1h_pct, "ret_4h_pct": cfg.ret_4h_pct},
        "baseline_filter": kpi(baseline_rows),
        "after_veto_filter": kpi(kept),
        "removed_sample": [
            {
                "symbol": t["symbol"],
                "opened_at": t["opened_at"],
                "pnl": round(float(t["pnl"]), 2),
                "ret_1h": t.get("ret_1h"),
                "ret_4h": t.get("ret_4h"),
                "exit_reason": t.get("exit_reason"),
            }
            for t in sorted(removed, key=lambda x: float(x["pnl"]))[:12]
        ],
    }


async def main() -> None:
    settings = get_settings()
    configure_logging("WARNING", json_output=False)
    desk = await _desk_kpi()
    print("DESK", json.dumps(desk), flush=True)

    frames = await _load_btc_frames()
    print(
        f"BTC bars 1h={len(frames.get('1h', []))} 4h={len(frames.get('4h', []))}",
        flush=True,
    )

    # Fast filter sweep on existing desk fills (all variants)
    filters = []
    for cfg in VARIANTS:
        row = await _counterfactual_on_desk_trades(frames, cfg)
        filters.append(row)
        a = row["after_veto_filter"]
        b = row["baseline_filter"]
        print(
            f"FILTER {cfg.name}: equity {b['equity']} → {a['equity']} "
            f"(Δ{a['equity']-b['equity']:+.2f}) WR {b['win_rate']}→{a['win_rate']} "
            f"removed={a['removed']} removed_pnl={a['removed_pnl']}",
            flush=True,
        )

    # Full rebuild for primary mid variant (most realistic with portfolio caps)
    top400 = await _top400()
    container = build_container(settings)
    paper = container.paper_trading
    provider = container.paper_price_provider
    primary = VARIANTS[1]  # mid_0.5_1.0

    print("REBUILD baseline ...", flush=True)
    baseline = await _run_sim(
        paper, provider, name=ACCT_BASE, symbols=top400, frames=frames, veto=None
    )
    print("BASELINE", json.dumps(baseline), flush=True)

    print(f"REBUILD veto {primary.name} ...", flush=True)
    vetoed = await _run_sim(
        paper, provider, name=ACCT_VETO, symbols=top400, frames=frames, veto=primary
    )
    print("VETO", json.dumps(vetoed), flush=True)

    await container.aclose()

    out = {
        "desk_live": desk,
        "primary_veto": primary.name,
        "definition": (
            "Veto new SHORT if last completed BTC 1h return >= ret_1h_pct "
            "AND last completed BTC 4h return >= ret_4h_pct (both required)."
        ),
        "filter_sweep": filters,
        "rebuild_baseline": baseline,
        "rebuild_veto": vetoed,
        "delta_rebuild": {
            "equity": round(vetoed["equity"] - baseline["equity"], 2),
            "win_rate_pp": round(vetoed["win_rate"] - baseline["win_rate"], 1),
            "closed": vetoed["closed"] - baseline["closed"],
            "profit_factor": round(vetoed["profit_factor"] - baseline["profit_factor"], 3),
            "shorts_vetoed_at_gate": vetoed["veto_stats"]["vetoed"],
        },
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2), flush=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
