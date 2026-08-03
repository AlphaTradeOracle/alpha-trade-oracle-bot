#!/usr/bin/env python3
"""Adversarial 100% audit: signals, paper math, desk, config. Emits JSON to stdout."""

from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
import urllib.request
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path


RESET = "2026-07-31T16:32:35+00:00"


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def _close(a, b, tol=0.05) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


async def main() -> int:
    from sqlalchemy import func, select, text

    from app.container import build_container
    from app.core.config import get_settings
    from app.core.enums import SignalDirection
    from app.core.logging import configure_logging
    from app.core.time import ensure_utc, utc_now
    from app.database.session import session_scope
    from app.models.market import Asset
    from app.models.paper import PaperFill, PaperPosition
    from app.models.signal import Signal
    from app.repositories.paper_repository import PaperRepository
    from app.services.desk_service import DeskService

    configure_logging("WARNING", json_output=False)
    settings = get_settings()
    container = build_container()
    now = utc_now()
    findings: list[dict] = []
    warnings: list[dict] = []
    ok_checks: list[str] = []

    def fail(code: str, msg: str, **extra):
        findings.append({"severity": "FAIL", "code": code, "msg": msg, **extra})

    def warn(code: str, msg: str, **extra):
        warnings.append({"severity": "WARN", "code": code, "msg": msg, **extra})

    def ok(code: str):
        ok_checks.append(code)

    # --- git / deploy drift (host passes via env; container has no /opt mount) ---
    head = os.environ.get("AUDIT_GIT_HEAD") or "?"
    origin = os.environ.get("AUDIT_GIT_ORIGIN") or "?"
    behind = os.environ.get("AUDIT_GIT_BEHIND") or "?"
    if head == "?" or behind == "?":
        warn("GIT_UNKNOWN", "AUDIT_GIT_* env not set by host wrapper")
    elif behind not in ("0",):
        warn(
            "GIT_DRIFT",
            f"VPS HEAD={head} behind origin/main={origin} by {behind} commits "
            "(expected after Rollback A — trendline commits still on remote)",
            head=head,
            origin=origin,
            behind=behind,
        )
    else:
        ok("GIT_IN_SYNC")

    # trendline must be absent after rollback
    import importlib.util

    if importlib.util.find_spec("app.indicators.trendlines") is not None:
        fail("TRENDLINE_PRESENT", "trendlines module still importable after Rollback A")
    else:
        ok("TRENDLINE_ABSENT")

    out: dict = {
        "generated_at": now.isoformat(),
        "head": head,
        "origin_main": origin,
        "behind_origin": behind,
        "reset_since": RESET,
    }

    async with session_scope() as session:
        account = await container.paper_trading.get_or_create_account(session)
        repo = PaperRepository(session)
        opens = await repo.list_open_positions(account.id)
        pendings = await repo.list_pending_positions(account.id)
        closed = await repo.list_closed(account.id, limit=5000)

        # cancelled count
        cancelled_n = (
            await session.execute(
                text(
                    "SELECT count(*) FROM paper_positions "
                    "WHERE account_id=:a AND status='cancelled'"
                ),
                {"a": account.id},
            )
        ).scalar_one()

        cash = Decimal(str(account.cash_balance))
        realized = Decimal(str(account.realized_pnl))
        initial = Decimal(str(account.initial_balance))
        open_margin = sum((p.margin_used or Decimal("0")) for p in opens)
        identity_ok = abs((cash + open_margin) - (initial + realized)) < Decimal("0.05")
        if identity_ok:
            ok("ACCOUNT_IDENTITY")
        else:
            fail(
                "ACCOUNT_IDENTITY",
                f"cash({cash})+margin({open_margin}) != initial({initial})+realized({realized})",
            )

        # Independent realized from closed positions
        sum_closed_pnl = sum((Decimal(str(p.realized_pnl or 0)) for p in closed), Decimal("0"))
        # also include fees if stored separately? usually realized_pnl is net
        if abs(sum_closed_pnl - realized) > Decimal("1.0"):
            # some books store fees outside — warn not fail if close
            if abs(sum_closed_pnl - realized) > Decimal("50"):
                fail(
                    "REALIZED_MISMATCH",
                    f"sum(closed.realized_pnl)={sum_closed_pnl} vs account.realized={realized}",
                )
            else:
                warn(
                    "REALIZED_DRIFT",
                    f"sum(closed.realized_pnl)={sum_closed_pnl} vs account.realized={realized}",
                )
        else:
            ok("REALIZED_MATCHES_CLOSED")

        # Win rate / PF independent
        wins = [p for p in closed if _f(p.realized_pnl) > 0]
        losses = [p for p in closed if _f(p.realized_pnl) < 0]
        flat = [p for p in closed if _f(p.realized_pnl) == 0]
        gross_win = sum(_f(p.realized_pnl) for p in wins)
        gross_loss = abs(sum(_f(p.realized_pnl) for p in losses))
        wr = (len(wins) / len(closed) * 100.0) if closed else 0.0
        pf = (gross_win / gross_loss) if gross_loss > 0 else None

        # Fill integrity
        pos_ids = [p.id for p in opens] + [p.id for p in closed]
        fill_counts: dict[int, int] = {}
        if pos_ids:
            rows = (
                await session.execute(
                    select(PaperFill.position_id, func.count())
                    .where(PaperFill.position_id.in_(pos_ids))
                    .group_by(PaperFill.position_id)
                )
            ).all()
            fill_counts = {int(i): int(c) for i, c in rows}
        closed_no_fills = [p.symbol for p in closed if fill_counts.get(p.id, 0) < 1]
        open_no_fills = [p.symbol for p in opens if fill_counts.get(p.id, 0) < 1]
        if closed_no_fills:
            fail("CLOSED_NO_FILLS", f"{len(closed_no_fills)} closed without fills", sample=closed_no_fills[:10])
        else:
            ok("CLOSED_HAVE_FILLS")
        if open_no_fills:
            fail("OPEN_NO_FILLS", f"{open_no_fills}")
        else:
            ok("OPEN_FILLS_OK")

        # Geometry: TP multiples vs R
        geom_bad = []
        for p in closed[:200] + opens:
            try:
                entry = _f(p.entry_price)
                sl = _f(p.stop_loss)
                if entry <= 0 or sl <= 0:
                    continue
                is_long = SignalDirection(p.direction).is_long
                r = (entry - sl) if is_long else (sl - entry)
                if r <= 0:
                    geom_bad.append({"symbol": p.symbol, "why": "non_positive_R"})
                    continue
                for name, tp in (
                    ("tp1", p.take_profit_1),
                    ("tp2", p.take_profit_2),
                    ("tp3", p.take_profit_3),
                ):
                    if tp is None:
                        continue
                    tpf = _f(tp)
                    mult = ((tpf - entry) / r) if is_long else ((entry - tpf) / r)
                    # expect ~2/4/6
                    if name == "tp1" and abs(mult - 2.0) > 0.15:
                        geom_bad.append({"symbol": p.symbol, "tp": name, "mult": round(mult, 3)})
                    if name == "tp2" and abs(mult - 4.0) > 0.2:
                        geom_bad.append({"symbol": p.symbol, "tp": name, "mult": round(mult, 3)})
                    if name == "tp3" and abs(mult - 6.0) > 0.25:
                        geom_bad.append({"symbol": p.symbol, "tp": name, "mult": round(mult, 3)})
            except Exception as exc:
                geom_bad.append({"symbol": getattr(p, "symbol", "?"), "why": str(exc)})
        if geom_bad:
            warn("TP_GEOMETRY", f"{len(geom_bad)} geometry quirks", sample=geom_bad[:15])
        else:
            ok("TP_GEOMETRY_2_4_6")

        # Stale pendings
        stale = []
        for p in pendings:
            exp = ensure_utc(p.expires_at) if p.expires_at else None
            if exp and now > exp:
                stale.append(p.symbol)
        if stale:
            fail("STALE_PENDING", f"{stale}")
        else:
            ok("NO_STALE_PENDING")

        # ========== SIGNAL FUNNEL ==========
        reset_dt = ensure_utc(
            __import__("datetime").datetime.fromisoformat(RESET.replace("Z", "+00:00"))
        )
        # column detection for is_actionable
        sig_cols = (
            await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='signals'"
                )
            )
        ).scalars().all()
        sig_cols_l = {c.lower() for c in sig_cols}

        # Schema at 10d5898 uses boolean is_dispatched (not dispatched_at).
        dispatch_expr = (
            "is_dispatched = true"
            if "is_dispatched" in sig_cols_l
            else ("dispatched_at IS NOT NULL" if "dispatched_at" in sig_cols_l else "false")
        )
        funnel_sql = text(
            f"""
            SELECT
              count(*) AS total,
              count(*) FILTER (WHERE direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')) AS actionable_dir,
              count(*) FILTER (WHERE direction IN ('STRONG_LONG','STRONG_SHORT')) AS strong,
              count(*) FILTER (WHERE score <= :smax AND direction IN ('SHORT','STRONG_SHORT')) AS short_gate_score,
              count(*) FILTER (WHERE score >= :lmin AND direction IN ('LONG','STRONG_LONG')) AS long_gate_score,
              count(*) FILTER (WHERE {dispatch_expr}) AS dispatched,
              count(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') AS last_24h,
              count(*) FILTER (
                WHERE created_at > NOW() - INTERVAL '24 hours'
                  AND direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')
              ) AS actionable_24h,
              count(*) FILTER (
                WHERE created_at > NOW() - INTERVAL '24 hours' AND {dispatch_expr}
              ) AS dispatched_24h
            FROM signals
            WHERE created_at >= :since
            """
        )
        row = (
            await session.execute(
                funnel_sql,
                {
                    "since": reset_dt,
                    "smax": float(settings.signal_short_max_score),
                    "lmin": float(settings.signal_min_score),
                },
            )
        ).mappings().one()
        funnel = dict(row)

        # Near-miss shorts: score just above short_max
        near = (
            await session.execute(
                text(
                    """
                    SELECT round(score::numeric, 2) AS score, count(*) AS n
                    FROM signals
                    WHERE created_at >= :since
                      AND direction IN ('SHORT','STRONG_SHORT')
                      AND score > :smax AND score <= :smax + 5
                    GROUP BY 1 ORDER BY 1
                    LIMIT 20
                    """
                ),
                {"since": reset_dt, "smax": float(settings.signal_short_max_score)},
            )
        ).mappings().all()
        funnel["short_near_miss_bins"] = [dict(r) for r in near]

        # Hourly actionable last 48h
        hourly = (
            await session.execute(
                text(
                    """
                    SELECT date_trunc('hour', created_at) AS h, count(*) AS n
                    FROM signals
                    WHERE created_at > NOW() - INTERVAL '48 hours'
                      AND direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')
                      AND (
                        (direction IN ('SHORT','STRONG_SHORT') AND score <= :smax AND score > :smin)
                        OR (direction IN ('LONG','STRONG_LONG') AND score >= :lmin)
                      )
                    GROUP BY 1 ORDER BY 1
                    """
                ),
                {
                    "smax": float(settings.signal_short_max_score),
                    "smin": float(getattr(settings, "signal_short_min_score", 0) or 0),
                    "lmin": float(settings.signal_min_score),
                },
            )
        ).mappings().all()
        funnel["paper_gate_hourly_48h"] = [
            {"h": r["h"].isoformat(), "n": int(r["n"])} for r in hourly
        ]

        # Scan / scheduled job health
        scan_jobs: list[dict] = []
        try:
            tables = set(
                (
                    await session.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema='public'"
                        )
                    )
                ).scalars().all()
            )
            if "scheduled_jobs" in tables:
                jobs = (
                    await session.execute(
                        text(
                            """
                            SELECT id, job_key, job_type, interval_seconds, is_enabled,
                                   last_run_at, last_success_at, next_run_at,
                                   last_status, last_error, run_count
                            FROM scheduled_jobs
                            ORDER BY job_key
                            """
                        )
                    )
                ).mappings().all()
                for r in jobs:
                    j = dict(r)
                    for k in ("last_run_at", "last_success_at", "next_run_at"):
                        if j.get(k) is not None:
                            j[k] = j[k].isoformat()
                    scan_jobs.append(j)
                    # stale scan?
                    if j.get("job_key") and "scan" in str(j["job_key"]).lower():
                        from app.core.time import ensure_utc as _eu

                        lr = r.get("last_success_at") or r.get("last_run_at")
                        if lr is not None:
                            age = (now - _eu(lr)).total_seconds() / 60.0
                            interval = int(j.get("interval_seconds") or 900)
                            if age > max(interval / 60.0 * 3, 45):
                                warn(
                                    "SCAN_STALE",
                                    f"{j['job_key']} last success {age:.0f}m ago (interval {interval}s)",
                                )
                            else:
                                ok("SCAN_FRESH")
                        if j.get("last_status") not in (None, "ok", "success", "completed"):
                            warn("SCAN_STATUS", f"{j['job_key']} status={j.get('last_status')} err={j.get('last_error')}")
                        if not j.get("is_enabled"):
                            fail("SCAN_DISABLED", f"{j['job_key']} disabled")
            elif "scan_jobs" in tables:
                jobs = (
                    await session.execute(
                        text(
                            """
                            SELECT id, status, started_at, finished_at
                            FROM scan_jobs ORDER BY id DESC LIMIT 8
                            """
                        )
                    )
                ).mappings().all()
                scan_jobs = [dict(r) for r in jobs]
            else:
                warn("SCAN_JOBS_UNAVAILABLE", f"no job tables; have={sorted(tables)[:30]}")
        except Exception as exc:
            warn("SCAN_JOBS_UNAVAILABLE", str(exc))

        # Universe
        uni = (
            await session.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (WHERE in_universe AND is_active) AS in_universe,
                      count(*) FILTER (WHERE is_active) AS active
                    FROM assets
                    """
                )
            )
        ).mappings().one()
        universe = dict(uni)
        target = int(settings.universe_target_count or 0)
        if target and abs(int(universe["in_universe"]) - target) > 15:
            warn(
                "UNIVERSE_SIZE",
                f"in_universe={universe['in_universe']} vs target={target}",
            )
        else:
            ok("UNIVERSE_NEAR_TARGET")

        # Last paper-gate signal age
        last_pg = (
            await session.execute(
                text(
                    """
                    SELECT max(created_at) AS last_at
                    FROM signals
                    WHERE direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')
                      AND (
                        (direction IN ('SHORT','STRONG_SHORT') AND score <= :smax AND score > :smin)
                        OR (direction IN ('LONG','STRONG_LONG') AND score >= :lmin)
                      )
                    """
                ),
                {
                    "smax": float(settings.signal_short_max_score),
                    "smin": float(getattr(settings, "signal_short_min_score", 0) or 0),
                    "lmin": float(settings.signal_min_score),
                },
            )
        ).scalar()
        if last_pg:
            age_h = (now - ensure_utc(last_pg)).total_seconds() / 3600.0
            funnel["last_paper_gate_signal"] = ensure_utc(last_pg).isoformat()
            funnel["last_paper_gate_age_hours"] = round(age_h, 2)
            if age_h > 6:
                warn("SIGNAL_STALE", f"last paper-gate signal {age_h:.1f}h ago")
            else:
                ok("SIGNALS_RECENT")
        else:
            fail("NO_PAPER_GATE_SIGNALS", "no signals pass paper score gates at all")

        # Dispatch vs paper: telegram only after fill — expect dispatched << actionable
        if int(funnel["dispatched_24h"]) > int(funnel["actionable_24h"]):
            fail("DISPATCH_GT_ACTIONABLE", "more dispatched than actionable in 24h — impossible?")
        if int(funnel.get("dispatched") or 0) == 0 and len(closed) > 0:
            warn(
                "ZERO_DISPATCH_WITH_TRADES",
                "closed trades exist but zero dispatched signals since reset "
                "(expected if telegram only on paper fill and dispatch flag unused)",
            )

        # ========== DESK SNAPSHOT ==========
        prices: dict[str, float] = {}
        for p in opens:
            try:
                prices[p.symbol.upper()] = await container.paper_price_provider.get_price(
                    p.symbol
                )
            except Exception as exc:
                warn("MARK_FAIL", f"{p.symbol}: {exc}")

        desk = DeskService(paper=container.paper_trading)
        snap = await desk.snapshot(session, prices=prices)
        # snap may be pydantic / dataclass / dict
        if hasattr(snap, "model_dump"):
            snap_d = snap.model_dump(by_alias=True)
        elif hasattr(snap, "dict"):
            snap_d = snap.dict()
        elif isinstance(snap, dict):
            snap_d = snap
        else:
            # DeskSnapshot object with attributes
            snap_d = {
                "portfolio": getattr(snap, "portfolio", None),
                "trades": getattr(snap, "trades", None),
                "recentTrades": getattr(snap, "recent_trades", None),
            }
            port = snap_d["portfolio"]
            if port is not None and not isinstance(port, dict):
                if hasattr(port, "model_dump"):
                    snap_d["portfolio"] = port.model_dump(by_alias=True)
                else:
                    snap_d["portfolio"] = {
                        k: getattr(port, k, None)
                        for k in (
                            "equity",
                            "cash",
                            "realized_pnl",
                            "realizedPnl",
                            "open_positions",
                            "openPositions",
                            "pending_orders",
                            "pendingOrders",
                            "closed_trades",
                            "closedTrades",
                            "win_rate_pct",
                            "winRatePct",
                            "total_return_pct",
                            "totalReturnPct",
                            "profit_factor",
                            "profitFactor",
                        )
                        if hasattr(port, k) or True
                    }

        # Use API for canonical camelCase
        api_snap = None
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:8000/api/v1/desk/snapshot", timeout=25
            ) as r:
                api_snap = json.loads(r.read().decode())
        except Exception as exc:
            fail("DESK_API", str(exc))

        pub_snap = None
        try:
            with urllib.request.urlopen(
                "https://alpha-trade-oracle.com/api/v1/desk/snapshot", timeout=25
            ) as r:
                pub_snap = json.loads(r.read().decode())
        except Exception as exc:
            fail("PUBLIC_DESK_API", str(exc))

        # Independent portfolio math
        unrealized = 0.0
        for p in opens:
            mark = prices.get(p.symbol.upper())
            if mark is None:
                continue
            side = 1.0 if SignalDirection(p.direction).is_long else -1.0
            unrealized += (mark - _f(p.entry_price)) * _f(p.remaining_quantity) * side
        indep_equity = _f(cash) + _f(open_margin) + unrealized
        indep_ret = (indep_equity - _f(initial)) / _f(initial) * 100.0 if initial else 0.0

        desk_compare = {
            "indep_equity": round(indep_equity, 4),
            "indep_cash": round(_f(cash), 4),
            "indep_realized": round(_f(realized), 4),
            "indep_wr": round(wr, 4),
            "indep_pf": round(pf, 4) if pf is not None else None,
            "indep_closed": len(closed),
            "indep_open": len(opens),
            "indep_pending": len(pendings),
            "indep_cancelled": int(cancelled_n),
            "indep_return_pct": round(indep_ret, 4),
        }

        def port_of(d):
            if not d:
                return {}
            return d.get("portfolio") or {}

        for label, snapx in (("local_api", api_snap), ("public", pub_snap)):
            p = port_of(snapx)
            if not p:
                continue
            eq = p.get("equity")
            if not _close(eq, indep_equity, 0.5):
                fail(
                    f"EQUITY_{label.upper()}",
                    f"{label} equity={eq} vs indep={indep_equity:.2f}",
                )
            else:
                ok(f"EQUITY_{label.upper()}")
            if not _close(p.get("cash"), _f(cash), 0.5):
                fail(f"CASH_{label.upper()}", f"{p.get('cash')} vs {_f(cash)}")
            else:
                ok(f"CASH_{label.upper()}")
            rp = p.get("realizedPnl") if p.get("realizedPnl") is not None else p.get("accountRealizedPnl")
            if not _close(rp, _f(realized), 0.5):
                fail(f"REALIZED_{label.upper()}", f"{rp} vs {_f(realized)}")
            else:
                ok(f"REALIZED_{label.upper()}")
            ct = p.get("closedTrades")
            if ct is not None and int(ct) != len(closed):
                # desk may cap recent list but closedTrades KPI should match
                fail(f"CLOSED_N_{label.upper()}", f"KPI closedTrades={ct} vs db={len(closed)}")
            else:
                ok(f"CLOSED_N_{label.upper()}")
            if int(p.get("openPositions") or 0) != len(opens):
                fail(f"OPEN_N_{label.upper()}", f"{p.get('openPositions')} vs {len(opens)}")
            else:
                ok(f"OPEN_N_{label.upper()}")
            if int(p.get("pendingOrders") or 0) != len(pendings):
                fail(
                    f"PENDING_N_{label.upper()}",
                    f"{p.get('pendingOrders')} vs {len(pendings)}",
                )
            else:
                ok(f"PENDING_N_{label.upper()}")
            wr_api = p.get("winRatePct")
            if wr_api is not None and abs(float(wr_api) - wr) > 0.6:
                fail(f"WR_{label.upper()}", f"{wr_api} vs indep {wr:.2f}")
            else:
                ok(f"WR_{label.upper()}")

        if api_snap and pub_snap:
            lp, pp = port_of(api_snap), port_of(pub_snap)
            diffs = []
            for k in (
                "equity",
                "cash",
                "realizedPnl",
                "accountRealizedPnl",
                "openPositions",
                "pendingOrders",
                "closedTrades",
                "winRatePct",
                "totalReturnPct",
                "profitFactor",
            ):
                a, b = lp.get(k), pp.get(k)
                if a is None and b is None:
                    continue
                try:
                    if abs(float(a) - float(b)) < 0.05:
                        continue
                except Exception:
                    if a == b:
                        continue
                diffs.append({"key": k, "local": a, "public": b})
            if diffs:
                fail("PUBLIC_NE_LOCAL", "public desk != local API", diffs=diffs)
            else:
                ok("PUBLIC_MATCHES_LOCAL")

        # Site health
        site_codes = {}
        for url in (
            "https://alpha-trade-oracle.com/",
            "https://alpha-trade-oracle.com/health",
            "https://alpha-trade-oracle.com/api/v1/desk/snapshot",
            "https://alpha-trade-oracle.com/api/v1/desk/top-coins?limit=5",
        ):
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=20) as r:
                    site_codes[url] = r.status
            except Exception as exc:
                site_codes[url] = f"ERR:{exc}"
                fail("SITE_HTTP", f"{url}: {exc}")
        if all(str(v).startswith("2") or v == 200 for v in site_codes.values()):
            ok("SITE_HTTP_OK")

        # Config snapshot — question everything
        config = {
            "signal_min_score": settings.signal_min_score,
            "signal_short_max_score": getattr(settings, "signal_short_max_score", None),
            "signal_short_min_score": getattr(settings, "signal_short_min_score", None),
            "signal_require_strong": getattr(settings, "signal_require_strong", None),
            "paper_retest_entry_enabled": settings.paper_retest_entry_enabled,
            "paper_max_open_positions": settings.paper_max_open_positions,
            "paper_max_open_per_direction": getattr(
                settings, "paper_max_open_per_direction", None
            ),
            "paper_use_perp_prices": settings.paper_use_perp_prices,
            "enable_paper_trading": settings.enable_paper_trading,
            "telegram_signal_dispatch": settings.telegram_signal_dispatch,
            "universe_target_count": settings.universe_target_count,
            "universe_require_leverage": settings.universe_require_leverage,
            "has_trendline_setting": hasattr(settings, "signal_trendline_gate_enabled"),
            "env_trendline": os.getenv("SIGNAL_TRENDLINE_GATE_ENABLED"),
        }

        # Questionable: all-signals rebuild inflated book
        if len(closed) > 40:
            warn(
                "BOOK_INFLATED_VS_ALLOWLIST_ERA",
                f"{len(closed)} closed after --all-signals rebuild; pre-rollback allowlist book was ~17. "
                "Not a math bug — universe scope change.",
                closed=len(closed),
            )

        # Questionable: cancelled >> closed
        if int(cancelled_n) > len(closed) * 3:
            warn(
                "HIGH_CANCEL_RATIO",
                f"cancelled={cancelled_n} vs closed={len(closed)} — retest skip-heavy (expected with retest on)",
            )

        out.update(
            {
                "config": config,
                "account": {
                    "cash": _f(cash),
                    "realized": _f(realized),
                    "initial": _f(initial),
                    "identity_ok": identity_ok,
                },
                "book": {
                    "open": len(opens),
                    "pending": len(pendings),
                    "closed": len(closed),
                    "cancelled": int(cancelled_n),
                    "wr_pct": round(wr, 2),
                    "pf": round(pf, 4) if pf is not None else None,
                    "gross_win": round(gross_win, 2),
                    "gross_loss": round(gross_loss, 2),
                    "flat": len(flat),
                },
                "desk_compare": desk_compare,
                "local_api_portfolio": port_of(api_snap),
                "public_portfolio": port_of(pub_snap),
                "signal_funnel": {
                    **{
                        k: int(v)
                        for k, v in funnel.items()
                        if k
                        not in (
                            "paper_gate_hourly_48h",
                            "short_near_miss_bins",
                            "last_paper_gate_signal",
                            "last_paper_gate_age_hours",
                        )
                        and v is not None
                        and not isinstance(v, (str, list, dict, float))
                    },
                    "last_paper_gate_signal": funnel.get("last_paper_gate_signal"),
                    "last_paper_gate_age_hours": funnel.get("last_paper_gate_age_hours"),
                    "paper_gate_hourly_48h": funnel.get("paper_gate_hourly_48h"),
                    "short_near_miss_bins": [
                        {"score": float(r["score"]), "n": int(r["n"])}
                        for r in funnel.get("short_near_miss_bins") or []
                    ],
                },
                "universe": universe,
                "scan_jobs": scan_jobs,
                "site_codes": site_codes,
                "findings": findings,
                "warnings": warnings,
                "ok_checks": ok_checks,
                "final_ok": len(findings) == 0,
                "fail_count": len(findings),
                "warn_count": len(warnings),
                "ok_count": len(ok_checks),
            }
        )

    print(json.dumps(out, indent=2, default=str))
    Path("/tmp/adversarial_full_audit.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    return 0 if not findings else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
