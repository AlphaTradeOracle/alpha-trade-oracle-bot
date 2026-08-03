"""Full live system audit: account, positions, universe, gates, stale pendings.

Run: python /app/scripts/vps_full_system_audit.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from decimal import Decimal


async def main() -> int:
    from sqlalchemy import func, select, text

    from app.container import build_container
    from app.core.config import get_settings
    from app.core.logging import configure_logging
    from app.core.time import ensure_utc, utc_now
    from app.database.session import session_scope
    from app.market_data.factory import create_paper_price_provider
    from app.market_data.leverage_coverage import LeverageCoverageClient, base_has_leverage
    from app.models.market import Asset
    from app.models.paper import PaperFill, PaperPosition
    from app.models.signal import Signal
    from app.repositories.paper_repository import PaperRepository

    configure_logging("WARNING", json_output=False)
    settings = get_settings()
    container = build_container()
    out: dict = {"generated_at": utc_now().isoformat(), "findings": [], "warnings": []}

    # --- config snapshot ---
    out["config"] = {
        "signal_min_score": settings.signal_min_score,
        "signal_short_max_score": getattr(settings, "signal_short_max_score", None),
        "signal_short_min_score": getattr(settings, "signal_short_min_score", None),
        "paper_retest_entry_enabled": settings.paper_retest_entry_enabled,
        "paper_use_perp_prices": settings.paper_use_perp_prices,
        "universe_require_leverage": settings.universe_require_leverage,
        "universe_target_count": settings.universe_target_count,
        "enable_paper_trading": settings.enable_paper_trading,
        "telegram_signal_dispatch": settings.telegram_signal_dispatch,
        "paper_leverage": settings.paper_leverage,
        "paper_margin_per_trade_usd": getattr(settings, "paper_margin_per_trade_usd", None),
        "paper_risk_per_trade_usd": getattr(settings, "paper_risk_per_trade_usd", None),
        "market_regime_hard_veto": getattr(settings, "market_regime_hard_veto", None),
        "regime_filter_enabled": getattr(settings, "regime_filter_enabled", None),
        "paper_update_interval_minutes": getattr(
            settings, "paper_update_interval_minutes", None
        ),
    }

    lev = LeverageCoverageClient(settings)
    router = create_paper_price_provider(settings)
    try:
        bases = await lev.fetch_tradable_bases()
        async with session_scope() as session:
            account = await container.paper_trading.get_or_create_account(session)
            repo = PaperRepository(session)
            opens = await repo.list_open_positions(account.id)
            pendings = await repo.list_pending_positions(account.id)
            closed = await repo.list_closed(account.id, limit=5000)

            # Account identity
            open_margin = sum((p.margin_used or Decimal("0")) for p in opens)
            cash = Decimal(str(account.cash_balance))
            realized = Decimal(str(account.realized_pnl))
            initial = Decimal(str(account.initial_balance))
            identity_ok = abs((cash + open_margin) - (initial + realized)) < Decimal("0.05")

            # Fill integrity for open+closed
            pos_ids = [p.id for p in opens] + [p.id for p in closed[:200]]
            fill_counts = {}
            if pos_ids:
                rows = (
                    await session.execute(
                        select(PaperFill.position_id, func.count())
                        .where(PaperFill.position_id.in_(pos_ids))
                        .group_by(PaperFill.position_id)
                    )
                ).all()
                fill_counts = {int(i): int(c) for i, c in rows}

            open_no_entry_fill = [
                p.symbol for p in opens if fill_counts.get(p.id, 0) < 1
            ]
            closed_no_fills = [
                p.symbol
                for p in closed
                if p.status == "closed" and fill_counts.get(p.id, 0) < 1
            ]

            # Stale pendings (wall clock past expires_at)
            now = utc_now()
            stale_pending = []
            for p in pendings:
                exp = ensure_utc(p.expires_at) if p.expires_at else None
                age_h = (now - ensure_utc(p.opened_at)).total_seconds() / 3600.0
                if exp and now > exp:
                    stale_pending.append(
                        {
                            "symbol": p.symbol,
                            "opened_at": p.opened_at.isoformat(),
                            "expires_at": exp.isoformat(),
                            "overdue_hours": round((now - exp).total_seconds() / 3600.0, 2),
                        }
                    )
                elif age_h > 72:
                    stale_pending.append(
                        {
                            "symbol": p.symbol,
                            "opened_at": p.opened_at.isoformat(),
                            "expires_at": exp.isoformat() if exp else None,
                            "age_hours": round(age_h, 2),
                            "note": "old_pending_no_expiry_or_within_expiry",
                        }
                    )

            # SL inside / wrong side of entry for open+pending
            level_issues = []
            for p in list(opens) + list(pendings):
                try:
                    entry = float(p.entry_price)
                    sl = float(p.stop_loss)
                    side = p.direction
                    if side == "long" and sl >= entry:
                        level_issues.append(
                            {"symbol": p.symbol, "status": p.status, "issue": "long_sl_ge_entry"}
                        )
                    if side == "short" and sl <= entry:
                        level_issues.append(
                            {"symbol": p.symbol, "status": p.status, "issue": "short_sl_le_entry"}
                        )
                    # pending: stop inside zone note?
                    notes = str(p.notes or "")
                    if "zone=" in notes and p.status == "pending":
                        # zone=lo-hi
                        part = notes.split("zone=")[1].split(";")[0]
                        if "ATR" not in part and "-" in part:
                            lo_s, hi_s = part.split("-", 1)
                            lo, hi = float(lo_s), float(hi_s)
                            if side == "long" and lo <= sl <= hi:
                                level_issues.append(
                                    {
                                        "symbol": p.symbol,
                                        "status": "pending",
                                        "issue": "long_sl_inside_retest_zone",
                                        "sl": sl,
                                        "zone": [lo, hi],
                                    }
                                )
                            if side == "short" and lo <= sl <= hi:
                                level_issues.append(
                                    {
                                        "symbol": p.symbol,
                                        "status": "pending",
                                        "issue": "short_sl_inside_retest_zone",
                                        "sl": sl,
                                        "zone": [lo, hi],
                                    }
                                )
                except Exception as exc:
                    level_issues.append(
                        {"symbol": p.symbol, "issue": f"parse_error:{exc}"}
                    )

            # Universe vs perp
            uni_rows = (
                await session.execute(
                    select(Asset.symbol, Asset.base_asset, Asset.market_cap_rank).where(
                        Asset.in_universe.is_(True)
                    )
                )
            ).all()
            no_route = []
            for symbol, base, rank in uni_rows:
                b = (base or "").upper()
                has_lev = base_has_leverage(b, bases) if b else False
                try:
                    await router.resolve_venue(symbol)
                except Exception as exc:
                    no_route.append(
                        {
                            "symbol": symbol,
                            "base": b,
                            "rank": rank,
                            "has_leverage": has_lev,
                            "error": type(exc).__name__,
                        }
                    )

            # Signals: pending paper without dispatched; recent undispatched high score
            pending_signal_ids = [p.signal_id for p in pendings if p.signal_id]
            undisp_pending = 0
            if pending_signal_ids:
                undisp_pending = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(Signal)
                            .where(
                                Signal.id.in_(pending_signal_ids),
                                Signal.is_dispatched.is_(False),
                            )
                        )
                    ).scalar_one()
                )

            # Win stats closed
            wins = sum(1 for p in closed if float(p.realized_pnl or 0) > 0)
            losses = sum(1 for p in closed if float(p.realized_pnl or 0) < 0)
            flat = sum(1 for p in closed if float(p.realized_pnl or 0) == 0)
            gp = sum(float(p.realized_pnl or 0) for p in closed if float(p.realized_pnl or 0) > 0)
            gl = abs(
                sum(float(p.realized_pnl or 0) for p in closed if float(p.realized_pnl or 0) < 0)
            )

            # Desk snapshot cross-check
            desk = None
            try:
                from app.services.desk_service import DeskService

                symbols = list({p.symbol for p in opens})
                prices: dict[str, float] = {}
                for sym in symbols:
                    try:
                        prices[sym.upper()] = await router.get_price(sym)
                    except Exception:
                        pass
                snap = await DeskService(paper=container.paper_trading).snapshot(
                    session, prices=prices
                )
                port = snap.portfolio
                desk = {
                    "equity": getattr(port, "equity", None),
                    "cash": getattr(port, "cash", None),
                    "openCount": getattr(port, "openCount", None),
                    "pendingCount": getattr(port, "pendingCount", None),
                    "closedCount": len(snap.closedTrades or []),
                    "pendingInBook": len(snap.pendingTrades or []),
                    "openInBook": len(snap.openTrades or []),
                }
            except Exception as exc:
                out["warnings"].append(f"desk_snapshot_failed:{type(exc).__name__}:{exc}")

            out["account"] = {
                "cash": float(cash),
                "realized_pnl": float(realized),
                "initial": float(initial),
                "open_margin": float(open_margin),
                "equity_cash_plus_margin": float(cash + open_margin),
                "identity_ok": identity_ok,
                "open": len(opens),
                "pending": len(pendings),
                "closed_sample": len(closed),
                "wins": wins,
                "losses": losses,
                "flat": flat,
                "profit_factor": (gp / gl) if gl > 0 else None,
            }
            out["stale_pending"] = stale_pending
            out["level_issues"] = level_issues
            out["open_no_entry_fill"] = open_no_entry_fill
            out["closed_no_fills_sample"] = closed_no_fills[:20]
            out["universe"] = {
                "in_universe": len(uni_rows),
                "leverage_bases": len(bases),
                "no_perp_route": no_route,
                "no_perp_route_count": len(no_route),
            }
            out["signals"] = {
                "pending_positions": len(pendings),
                "pending_signals_undispatched": undisp_pending,
            }
            out["desk"] = desk

            if not identity_ok:
                out["findings"].append(
                    {
                        "severity": "critical",
                        "id": "account_identity",
                        "detail": "cash+margin != initial+realized",
                    }
                )
            if stale_pending:
                out["findings"].append(
                    {
                        "severity": "high",
                        "id": "stale_pending",
                        "count": len(stale_pending),
                        "symbols": [x["symbol"] for x in stale_pending],
                    }
                )
            if level_issues:
                out["findings"].append(
                    {
                        "severity": "high",
                        "id": "level_geometry",
                        "count": len(level_issues),
                        "items": level_issues,
                    }
                )
            if no_route:
                out["findings"].append(
                    {
                        "severity": "high",
                        "id": "universe_no_perp_route",
                        "count": len(no_route),
                        "symbols": [x["symbol"] for x in no_route],
                    }
                )
            if open_no_entry_fill:
                out["findings"].append(
                    {
                        "severity": "critical",
                        "id": "open_without_entry_fill",
                        "symbols": open_no_entry_fill,
                    }
                )

        print(json.dumps(out, indent=2, default=str))
        return 0
    finally:
        await lev.aclose()
        close = getattr(router, "close", None)
        if close:
            await close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
