"""Independent recomputation of every desk snapshot numeric field."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from app.container import build_container
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.repositories.paper_repository import PaperRepository
from app.services.desk_service import DeskService


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
    configure_logging("WARNING", json_output=False)
    container = build_container()
    issues: list[str] = []
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
                print(f"  {tag:4} {name:22} api={api_v} calc={calc_v}")

            # Identity
            identity = initial + ledger_realized
            cash_margin = cash + open_margin
            print(
                f"  {'OK' if _close(cash_margin, identity) else 'FAIL':4} "
                f"cash+margin vs initial+realized: {cash_margin:.4f} vs {identity:.4f}"
            )
            if not _close(cash_margin, identity):
                issues.append("account_identity_broken")

            # closedTrades cap risk
            if len(all_closed) > 500:
                issues.append(
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
                    trade_ok += 1
                    print(f"  OK   PENDING {p.symbol}")

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
            reg = snap.market_regime
            if reg is None:
                print("  WARN marketRegime=null")
            else:
                d = reg if isinstance(reg, dict) else reg.model_dump() if hasattr(reg, "model_dump") else vars(reg)
                # DeskMarketRegime is a dataclass/pydantic — print key nums
                for key in (
                    "global_score",
                    "btc_d",
                    "usdt_d",
                    "fear_greed",
                    "liquidity_score",
                    "available",
                    "bias",
                ):
                    # try both snake and camel via getattr
                    val = getattr(reg, key, None)
                    if val is None:
                        camel = "".join(
                            w.capitalize() if i else w
                            for i, w in enumerate(key.split("_"))
                        )
                        # already camel variants
                        for alt in (key, camel, key.replace("_", "")):
                            if hasattr(reg, alt):
                                val = getattr(reg, alt)
                                break
                    print(f"  {key}={val}")

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
            print(f"FINAL_OK={len(issues)==0}")

            # dump compact portfolio for UI cross-check
            print("=== SNAPSHOT_PORTFOLIO_JSON ===")
            print(
                json.dumps(
                    {
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
                    indent=2,
                )
            )
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
