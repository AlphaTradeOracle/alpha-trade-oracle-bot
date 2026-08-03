"""Independent recomputation of every desk snapshot numeric field."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import timedelta
from pathlib import Path

from app.container import build_container
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.repositories.paper_repository import PaperRepository
from app.services.desk_service import DeskService, _parse_zone, _pending_retest_zone


def _pct(a: float, b: float) -> float | None:
    if b == 0:
        return None
    return (a - b) / b * 100.0


def _close(a: float | None, b: float | None, tol: float = 0.05) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/desk_math_audit.json")
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    container = build_container()
    issues: list[str] = []
    warnings: list[str] = []
    portfolio_rows: list[dict] = []
    trade_rows: list[dict] = []
    pending_rows: list[dict] = []
    try:
        async with session_scope() as session:
            account = await container.paper_trading.get_or_create_account(session)
            repo = PaperRepository(session)
            opens = await repo.list_open_positions(account.id)
            pendings = await repo.list_pending_positions(account.id)
            closed = await repo.list_closed(account.id, limit=500)
            all_closed = await repo.list_closed(account.id, limit=5000)

            prices: dict[str, float] = {}
            for p in opens:
                try:
                    prices[p.symbol.upper()] = await container.paper_price_provider.get_price(
                        p.symbol
                    )
                except Exception as exc:
                    issues.append(f"MARK_FAIL {p.symbol}: {exc}")

            desk = DeskService(paper=container.paper_trading)
            snap = await desk.snapshot(session, prices=prices)

            # --- Portfolio recomputation ---
            open_margin = sum(float(p.margin_used) for p in opens)
            unrealized = 0.0
            for p in opens:
                mark = prices.get(p.symbol.upper())
                if mark is None:
                    continue
                side = 1.0 if SignalDirection(p.direction).is_long else -1.0
                unrealized += (mark - float(p.entry_price)) * float(p.remaining_quantity) * side

            cash = float(account.cash_balance)
            initial = float(account.initial_balance)
            equity = cash + open_margin + unrealized
            total_return = (equity - initial) / initial * 100.0 if initial else 0.0
            wins = [p for p in closed if float(p.realized_pnl) > 0]
            win_rate = (len(wins) / len(closed) * 100.0) if closed else 0.0
            ledger_realized = float(account.realized_pnl)
            closed_realized = sum(float(p.realized_pnl) for p in closed)
            open_realized = sum(float(p.realized_pnl) for p in opens)

            port = snap.portfolio

            def _p(name: str):
                # DeskPortfolio is camelCase pydantic
                return getattr(port, name)

            checks = [
                ("totalCapital", _p("totalCapital"), initial),
                ("cash", _p("cash"), cash),
                ("equity", _p("equity"), equity),
                ("marginLocked", _p("marginLocked"), open_margin),
                ("openUpnl", _p("openUpnl"), unrealized),
                ("totalReturnPct", _p("totalReturnPct"), total_return),
                ("winRatePct", _p("winRatePct"), win_rate),
                ("openPositions", float(_p("openPositions")), float(len(opens))),
                ("pendingOrders", float(_p("pendingOrders")), float(len(pendings))),
                ("closedTrades", float(_p("closedTrades")), float(len(closed))),
                ("accountRealizedPnl", _p("accountRealizedPnl"), ledger_realized),
                ("realizedPnl(closed)", _p("realizedPnl"), closed_realized),
                ("openRealizedPnl", _p("openRealizedPnl"), open_realized),
            ]

            print("=== PORTFOLIO ===")
            for name, api_v, calc_v in checks:
                ok = _close(float(api_v) if api_v is not None else None, calc_v)
                tag = "OK" if ok else "FAIL"
                if not ok:
                    issues.append(f"{name}: api={api_v} calc={calc_v}")
                portfolio_rows.append(
                    {
                        "field": name,
                        "api": api_v,
                        "calc": round(calc_v, 6) if isinstance(calc_v, float) else calc_v,
                        "ok": ok,
                    }
                )
                print(f"  {tag:4} {name:22} api={api_v} calc={calc_v}")

            # Identity
            identity = initial + ledger_realized
            cash_margin = cash + open_margin
            id_ok = _close(cash_margin, identity)
            print(
                f"  {'OK' if id_ok else 'FAIL':4} "
                f"cash+margin vs initial+realized: {cash_margin:.4f} vs {identity:.4f}"
            )
            portfolio_rows.append(
                {
                    "field": "cash+margin == initial+realized",
                    "api": round(cash_margin, 4),
                    "calc": round(identity, 4),
                    "ok": id_ok,
                }
            )
            if not id_ok:
                issues.append("account_identity_broken")

            # closedTrades cap risk
            if len(all_closed) > 500:
                warnings.append(
                    f"CLOSED_CAP: summary uses 500 but DB has {len(all_closed)} closed"
                )
                print(f"  WARN CLOSED_CAP all_closed={len(all_closed)} summary_cap=500")

            # --- Trades ---
            print("=== TRADES ===")
            api_trades = {str(t.id): t for t in snap.trades}
            trade_ok = 0
            trade_fail = 0
            for p in opens + list(pendings) + list(closed):
                t = api_trades.get(str(p.id))
                if t is None:
                    # closed without exit fill are dropped by design
                    if p.status == "closed":
                        print(f"  SKIP closed id={p.id} {p.symbol} (no desk trade row)")
                        continue
                    issues.append(f"MISSING_TRADE {p.id} {p.symbol} {p.status}")
                    trade_fail += 1
                    print(f"  FAIL MISSING {p.id} {p.symbol}")
                    continue

                mark = prices.get(p.symbol.upper()) if p.status == "open" else None
                if p.status == "open" and mark is not None:
                    side = 1.0 if SignalDirection(p.direction).is_long else -1.0
                    upnl = (mark - float(p.entry_price)) * float(p.remaining_quantity) * side
                    risk = float(p.risk_amount or 0)
                    rem_frac = (
                        float(p.remaining_quantity) / float(p.initial_quantity)
                        if float(p.initial_quantity)
                        else 1.0
                    )
                    risk_rem = risk * rem_frac
                    r = (upnl / risk_rem) if risk_rem > 0 else None
                    margin = float(p.margin_used)
                    profit_pct_margin = (upnl / margin * 100.0) if margin else None

                    row_ok = (
                        _close(t.upnl, upnl)
                        and _close(t.entry, float(p.entry_price))
                        and _close(t.mark, mark, tol=0.05)
                    )
                    if t.r is not None and r is not None and not _close(t.r, r, tol=0.05):
                        issues.append(
                            f"R_MISMATCH {p.symbol}: api_r={t.r} calc_r={r} "
                            f"(margin_pct would be {profit_pct_margin})"
                        )
                        row_ok = False
                    tag = "OK" if row_ok else "FAIL"
                    if not row_ok:
                        trade_fail += 1
                        issues.append(f"OPEN_TRADE {p.symbol} upnl api={t.upnl} calc={upnl}")
                    else:
                        trade_ok += 1
                    print(
                        f"  {tag:4} OPEN {p.symbol:12} upnl api={t.upnl} calc={upnl:.4f} "
                        f"r_api={t.r} r_calc={r} margin%={profit_pct_margin}"
                    )
                elif p.status == "closed":
                    risk = float(p.risk_amount or 0)
                    rpnl = float(p.realized_pnl)
                    r = (rpnl / risk) if risk > 0 else None
                    margin = float(p.margin_used) or float(getattr(t, "margin", 0) or 0)
                    # margin may be 0 after close — desk restores entry margin
                    row_ok = _close(t.realized, rpnl)
                    if t.r is not None and r is not None and not _close(t.r, r, tol=0.05):
                        row_ok = False
                        issues.append(f"CLOSED_R {p.symbol}: api={t.r} calc={r}")
                    tag = "OK" if row_ok else "FAIL"
                    if not row_ok:
                        trade_fail += 1
                    else:
                        trade_ok += 1
                    print(
                        f"  {tag:4} CLOSED {p.symbol:12} realized api={t.realized} calc={rpnl:.4f} "
                        f"r_api={t.r} r_calc={r}"
                    )
                else:
                    # Pending: entry zone must be price levels, not ATR multipliers
                    notes = str(p.notes or "")
                    calc_lo, calc_hi = _pending_retest_zone(
                        notes,
                        entry=float(p.entry_price),
                        direction=p.direction,
                    )
                    fake_atr = (
                        t.entryZoneLow is not None
                        and t.entryZoneHigh is not None
                        and 0.0 < float(t.entryZoneLow) < 2.0
                        and 0.0 < float(t.entryZoneHigh) <= 2.0
                        and "ATR" in notes.upper()
                        and _parse_zone(notes) == (None, None)
                        and abs(float(t.entryZoneLow) - 0.55) < 0.02
                    )
                    zone_ok = (
                        _close(t.entryZoneLow, calc_lo, tol=1e-6)
                        and _close(t.entryZoneHigh, calc_hi, tol=1e-6)
                        and not fake_atr
                    )
                    # Short: stop should be above zone high; long: below zone low.
                    # Desk display can still be correct when geometry is tight.
                    structure_ok = True
                    if (
                        calc_lo is not None
                        and calc_hi is not None
                        and float(p.stop_loss or 0) > 0
                    ):
                        sl = float(p.stop_loss)
                        if SignalDirection(p.direction).is_short:
                            structure_ok = sl >= calc_hi - 1e-12
                        else:
                            structure_ok = sl <= calc_lo + 1e-12
                    display_ok = zone_ok and (not fake_atr) and _close(
                        t.entry, float(p.entry_price)
                    )
                    if not display_ok:
                        trade_fail += 1
                        issues.append(
                            f"PENDING {p.symbol}: zone api={t.entryZoneLow}-{t.entryZoneHigh} "
                            f"calc={calc_lo}-{calc_hi} fake_atr={fake_atr}"
                        )
                    else:
                        trade_ok += 1
                    if not structure_ok:
                        warnings.append(
                            f"STOP_INSIDE_ZONE {p.symbol}: zone={calc_lo}-{calc_hi} "
                            f"stop={p.stop_loss} (desk OK, risk geometry tight)"
                        )
                    pending_rows.append(
                        {
                            "symbol": p.symbol,
                            "score": float(p.signal_score or 0),
                            "entry": float(p.entry_price),
                            "zone_lo": t.entryZoneLow,
                            "zone_hi": t.entryZoneHigh,
                            "stop": float(p.stop_loss or 0),
                            "ok": display_ok,
                            "structure_ok": structure_ok,
                            "fake_atr_zone": fake_atr,
                        }
                    )
                    tag = "OK" if display_ok and structure_ok else ("WARN" if display_ok else "FAIL")
                    print(
                        f"  {tag:4} PENDING {p.symbol:12} zone={t.entryZoneLow}-{t.entryZoneHigh} "
                        f"stop={p.stop_loss}"
                    )

            print(f"  trade_rows ok={trade_ok} fail={trade_fail}")

            # --- Equity curve consistency ---
            print("=== EQUITY CURVE ===")
            pts = snap.equity
            if pts:
                last = pts[-1]
                print(f"  points={len(pts)} first={pts[0].equity} last={last.equity}")
                # live equity should match last point closely (MTM append)
                if not _close(last.equity, port.equity, tol=0.5):
                    issues.append(
                        f"CURVE_LAST vs portfolio.equity: {last.equity} vs {port.equity}"
                    )
                    print(f"  FAIL curve_last={last.equity} portfolio.equity={port.equity}")
                else:
                    print("  OK   curve_last ~= portfolio.equity")

                # Performance windows (same as frontend)
                from datetime import datetime as _dt

                now = utc_now()
                live = float(last.equity)
                print("  Performance windows (client formula):")
                for label, hours in [("1h", 1), ("24h", 24), ("7D", 24 * 7), ("30D", 24 * 30)]:
                    cutoff = now - timedelta(hours=hours)
                    baseline = None
                    for pt in pts:
                        raw = getattr(pt, "t", None) or getattr(pt, "time", None)
                        if isinstance(raw, str):
                            ts = ensure_utc(_dt.fromisoformat(raw.replace("Z", "+00:00")))
                        else:
                            ts = ensure_utc(raw)
                        if ts <= cutoff:
                            baseline = float(pt.equity)
                    if baseline is None:
                        baseline = float(pts[0].equity)
                    pct = _pct(live, baseline)
                    print(f"    {label:4} baseline={baseline:.2f} live={live:.2f} pct={pct}")
            else:
                issues.append("EMPTY_EQUITY_CURVE")
                print("  FAIL empty equity curve")

            # --- Regime presence ---
            print("=== MARKET REGIME ===")
            reg = getattr(snap, "marketRegime", None) or getattr(snap, "market_regime", None)
            if reg is None:
                print("  WARN marketRegime=null")
                warnings.append("marketRegime=null (snapshot without regime payload)")
            else:
                for key in (
                    "globalScore",
                    "global_score",
                    "btcD",
                    "btc_d",
                    "usdtD",
                    "fearGreed",
                    "liquidityScore",
                    "available",
                    "bias",
                ):
                    if hasattr(reg, key):
                        print(f"  {key}={getattr(reg, key)}")

            print("=== SEMANTIC RISKS (UI) ===")
            print(
                "  Realized KPI uses accountRealizedPnl (ledger incl. open scale-outs); "
                f"ledger={ledger_realized:.2f} closed_only={closed_realized:.2f} "
                f"open_partials={open_realized:.2f}"
            )
            if abs(ledger_realized - closed_realized) > 0.05:
                print(
                    "  WARN Realized tooltip 'closed only' would be WRONG "
                    f"(delta={ledger_realized - closed_realized:.2f})"
                )
                issues.append(
                    f"REALIZED_TOOLTIP_MISMATCH ledger={ledger_realized:.2f} "
                    f"closed={closed_realized:.2f}"
                )

            print("=== SUMMARY ===")
            print(f"issues={len(issues)}")
            for i in issues:
                print(f"  - {i}")
            print(f"warnings={len(warnings)}")
            for w in warnings:
                print(f"  - {w}")
            print(f"FINAL_OK={len(issues)==0}")

            out = {
                "generated_at": utc_now().isoformat(),
                "final_ok": len(issues) == 0,
                "issue_count": len(issues),
                "warning_count": len(warnings),
                "issues": issues,
                "warnings": warnings,
                "portfolio": {
                    "totalCapital": _p("totalCapital"),
                    "equity": _p("equity"),
                    "cash": _p("cash"),
                    "accountRealizedPnl": _p("accountRealizedPnl"),
                    "realizedPnl": _p("realizedPnl"),
                    "openRealizedPnl": _p("openRealizedPnl"),
                    "openUpnl": _p("openUpnl"),
                    "totalReturnPct": _p("totalReturnPct"),
                    "winRatePct": _p("winRatePct"),
                    "marginLocked": _p("marginLocked"),
                    "openPositions": _p("openPositions"),
                    "pendingOrders": _p("pendingOrders"),
                    "closedTrades": _p("closedTrades"),
                    "equityChangePct": _p("equityChangePct"),
                    "realizedChangePct": _p("realizedChangePct"),
                    "openR": _p("openR"),
                },
                "portfolio_checks": portfolio_rows,
                "trade_ok": trade_ok,
                "trade_fail": trade_fail,
                "pending_checks": pending_rows,
                "open_symbols": [p.symbol for p in opens],
                "closed_n": len(closed),
                "pending_n": len(pendings),
                "open_n": len(opens),
            }
            Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
            print(f"WROTE {args.out}")
            print("=== SNAPSHOT_PORTFOLIO_JSON ===")
            print(json.dumps(out["portfolio"], indent=2))
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
