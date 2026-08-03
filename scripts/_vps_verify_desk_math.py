#!/usr/bin/env python3
"""Cross-check desk snapshot math against paper ledger + trade mapping."""
from __future__ import annotations

import json
import math
import sys
import urllib.request
from collections import defaultdict
from decimal import Decimal

import psycopg


DSN = "postgresql://alpha_trade_oracle:${POSTGRES_PASSWORD}@127.0.0.1:5432/alpha_trade_oracle"
DESK_URL = "http://127.0.0.1:8000/api/v1/desk/snapshot"


def f(x) -> float:
    return float(x or 0)


def near(a: float, b: float, tol: float = 0.02) -> bool:
    return abs(a - b) <= tol


def main() -> int:
    import os

    pw = os.environ["POSTGRES_PASSWORD"]
    dsn = (
        f"postgresql://alpha_trade_oracle:{pw}@127.0.0.1:5432/alpha_trade_oracle"
    )

    with urllib.request.urlopen(DESK_URL, timeout=30) as resp:
        desk = json.load(resp)

    p = desk["portfolio"]
    open_trades = desk.get("openTrades") or []
    closed_trades = desk.get("closedTrades") or []
    pending_trades = desk.get("pendingTrades") or []
    equity_curve = desk.get("equityCurve") or desk.get("equity") or []

    issues: list[str] = []
    ok: list[str] = []

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, initial_balance, cash_balance, realized_pnl,
                       margin_per_trade, leverage
                FROM paper_accounts WHERE name='default'
                """
            )
            acc = cur.fetchone()
            assert acc
            acc_id, initial, cash, acc_realized, mpt, lev = acc
            initial, cash, acc_realized, mpt = map(f, (initial, cash, acc_realized, mpt))
            lev = f(lev)

            cur.execute(
                """
                SELECT id, symbol, status, direction,
                       entry_price, stop_loss, current_stop,
                       initial_quantity, remaining_quantity,
                       margin_used, notional, leverage,
                       realized_pnl, fees, risk_amount,
                       tp1_filled, tp2_filled, tp3_filled
                FROM paper_positions
                WHERE account_id=%s
                ORDER BY id
                """,
                (acc_id,),
            )
            cols = [d.name for d in cur.description]
            positions = [dict(zip(cols, row)) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT f.id, f.position_id, f.reason, f.price, f.quantity, f.fee, f.pnl
                FROM paper_fills f
                JOIN paper_positions p ON p.id = f.position_id
                WHERE p.account_id=%s
                """,
                (acc_id,),
            )
            fcols = [d.name for d in cur.description]
            fills = [dict(zip(fcols, row)) for row in cur.fetchall()]

    by_status = defaultdict(list)
    for pos in positions:
        by_status[pos["status"]].append(pos)

    open_pos = by_status.get("open", [])
    closed_pos = by_status.get("closed", [])
    pending_pos = by_status.get("pending", [])

    open_margin = sum(f(p["margin_used"]) for p in open_pos)
    closed_realized = sum(f(p["realized_pnl"]) for p in closed_pos)
    open_realized = sum(f(p["realized_pnl"]) for p in open_pos)
    sum_all_realized = sum(f(p["realized_pnl"]) for p in positions)

    # Account invariant
    lhs = cash + open_margin
    rhs = initial + acc_realized
    if near(lhs, rhs, 0.05):
        ok.append(f"cash+open_margin == initial+realized ({lhs:.2f})")
    else:
        issues.append(f"ACCOUNT INVARIANT FAIL cash+margin={lhs:.2f} vs initial+realized={rhs:.2f}")

    if near(acc_realized, sum_all_realized, 0.05):
        ok.append(f"account.realized == sum(position.realized) ({acc_realized:.2f})")
    else:
        issues.append(
            f"account.realized {acc_realized:.2f} != sum positions {sum_all_realized:.2f}"
        )

    if near(mpt, 300, 0.01):
        ok.append("margin_per_trade=300")
    else:
        issues.append(f"margin_per_trade={mpt} expected 300")

    # Desk portfolio fields
    checks = [
        ("totalCapital", f(p["totalCapital"]), initial),
        ("cash", f(p["cash"]), cash),
        ("marginLocked", f(p["marginLocked"]), open_margin),
        ("realizedPnl(closed)", f(p["realizedPnl"]), closed_realized),
        ("openRealizedPnl", f(p.get("openRealizedPnl") or 0), open_realized),
        ("accountRealizedPnl", f(p.get("accountRealizedPnl") or 0), acc_realized),
        ("openPositions", f(p["openPositions"]), float(len(open_pos))),
        ("pendingOrders", f(p["pendingOrders"]), float(len(pending_pos))),
        ("closedTrades", f(p["closedTrades"]), float(len(closed_pos))),
    ]
    for name, got, exp in checks:
        if near(got, exp, 0.05 if "Positions" not in name and "Trades" not in name and "Orders" not in name else 0):
            ok.append(f"desk.{name}={got}")
        else:
            issues.append(f"desk.{name}={got} expected {exp}")

    # Equity ≈ cash + open_margin + open_upnl (mark-to-market)
    desk_equity = f(p["equity"])
    desk_upnl = f(p["openUpnl"])
    equity_cash_margin_upnl = cash + open_margin + desk_upnl
    if near(desk_equity, equity_cash_margin_upnl, 0.5):
        ok.append(f"equity≈cash+margin+upnl ({desk_equity:.2f})")
    else:
        issues.append(
            f"equity {desk_equity:.2f} vs cash+margin+upnl {equity_cash_margin_upnl:.2f} "
            f"(cash={cash:.2f} margin={open_margin:.2f} upnl={desk_upnl:.2f})"
        )

    total_return = ((desk_equity - initial) / initial) * 100 if initial else 0
    if near(f(p["totalReturnPct"]), total_return, 0.05):
        ok.append(f"totalReturnPct={p['totalReturnPct']}")
    else:
        issues.append(
            f"totalReturnPct {p['totalReturnPct']} vs recomputed {total_return:.2f}"
        )

    # Winrate from closed
    wins = sum(1 for pos in closed_pos if f(pos["realized_pnl"]) > 0)
    wr = (wins / len(closed_pos) * 100) if closed_pos else 0
    if near(f(p.get("winRatePct") or 0), wr, 0.15):
        ok.append(f"winRatePct={p.get('winRatePct')} (wins={wins}/{len(closed_pos)})")
    else:
        issues.append(
            f"winRatePct {p.get('winRatePct')} vs {wr:.1f} ({wins}/{len(closed_pos)})"
        )

    # Map desk open trades vs DB
    desk_open_by_sym = {t["symbol"]: t for t in open_trades}
    for pos in open_pos:
        sym = pos["symbol"]
        t = desk_open_by_sym.get(sym)
        if not t:
            issues.append(f"OPEN {sym} missing on desk")
            continue
        risk = f(pos["risk_amount"])
        rem = f(pos["remaining_quantity"])
        ini = f(pos["initial_quantity"])
        notional = f(pos["notional"])
        lev_p = f(pos["leverage"]) or lev
        margin_used = f(pos["margin_used"])
        # Desk margin for open = remaining share of initial margin (or margin_used)
        initial_notional = notional if notional > 0 else ini * f(pos["entry_price"])
        initial_margin = initial_notional / lev_p if lev_p else 0
        share = rem / ini if ini > 0 else 0
        # UI shows margin_used when >0 else initial_margin
        expected_margin_ui = margin_used if margin_used > 0 else initial_margin
        if not near(f(t.get("margin") or 0), expected_margin_ui, 0.05):
            issues.append(
                f"{sym} margin desk={t.get('margin')} db_used={margin_used} "
                f"expected_ui={expected_margin_ui:.2f}"
            )
        else:
            ok.append(f"{sym} margin OK ({t.get('margin')})")

        if not near(f(t.get("notional") or 0), initial_notional * share, 1.0):
            # allow rounding on coin qty
            if not near(f(t.get("notional") or 0), notional * share, 1.0):
                issues.append(
                    f"{sym} notional desk={t.get('notional')} "
                    f"expected≈{initial_notional * share:.2f}"
                )

        # Profit % = upnl / margin * 100
        upnl = t.get("upnl")
        margin_ui = f(t.get("margin") or 0)
        if upnl is not None and margin_ui > 0:
            pct = f(upnl) / margin_ui * 100
            # r should be upnl/risk * remaining share roughly; check profit% consistency
            if risk > 0 and t.get("r") is not None:
                # open R often scaled; profit% uses margin
                pass
            ok.append(f"{sym} profit%≈{pct:+.2f}% (upnl={upnl}, margin={margin_ui})")

        # risk_amount should be ~300 for fixed margin book
        if not near(risk, 300, 0.05):
            issues.append(f"{sym} risk_amount={risk} expected ~300")

        if not near(notional, 3000, 1.0):
            issues.append(f"{sym} notional={notional} expected ~3000")

    # Closed desk sample: margin display = initial margin (notional/lev), profit%
    desk_closed_by_id = {str(t["id"]): t for t in closed_trades}
    closed_checked = 0
    for pos in closed_pos:
        tid = str(pos["id"])
        # desk id might be "paper-{id}" or just id
        t = desk_closed_by_id.get(tid) or desk_closed_by_id.get(f"paper-{tid}")
        if t is None:
            # try match by symbol+opened — skip if list truncated
            matches = [x for x in closed_trades if x.get("symbol") == pos["symbol"]]
            t = matches[0] if len(matches) == 1 else None
        if t is None:
            continue
        closed_checked += 1
        notional = f(pos["notional"])
        lev_p = f(pos["leverage"]) or lev
        initial_margin = notional / lev_p if lev_p else 0
        if not near(f(t.get("margin") or 0), initial_margin, 0.05):
            issues.append(
                f"CLOSED {pos['symbol']} margin desk={t.get('margin')} expected {initial_margin:.2f}"
            )
        realized = f(pos["realized_pnl"])
        if not near(f(t.get("realized") or 0), realized, 0.05):
            issues.append(
                f"CLOSED {pos['symbol']} realized desk={t.get('realized')} db={realized}"
            )
        risk = f(pos["risk_amount"])
        if risk > 0 and t.get("r") is not None:
            exp_r = realized / risk
            if not near(f(t["r"]), exp_r, 0.02):
                issues.append(
                    f"CLOSED {pos['symbol']} r desk={t['r']} expected {exp_r:.2f}"
                )
        if initial_margin > 0:
            pct = realized / initial_margin * 100
            # website profit% uses realized/margin
            ok.append(f"CLOSED {pos['symbol']} profit%={pct:+.2f}%")

    if closed_checked == 0 and closed_pos:
        issues.append("could not match any closed trades desk↔db")

    # Counts on desk lists
    if len(open_trades) != len(open_pos):
        issues.append(f"openTrades len {len(open_trades)} != db {len(open_pos)}")
    else:
        ok.append(f"openTrades count {len(open_trades)}")
    if len(closed_trades) != len(closed_pos):
        issues.append(f"closedTrades len {len(closed_trades)} != db {len(closed_pos)}")
    else:
        ok.append(f"closedTrades count {len(closed_trades)}")

    # KPI total realized (what website shows)
    kpi_realized = f(p.get("accountRealizedPnl") or 0)
    if near(kpi_realized, acc_realized, 0.05):
        ok.append(f"KPI total realized = {kpi_realized:.2f}")
    else:
        issues.append(f"KPI realized mismatch {kpi_realized} vs {acc_realized}")

    # Fill sum vs position realized
    fill_pnl_by_pos = defaultdict(float)
    fill_fee_by_pos = defaultdict(float)
    for fl in fills:
        fill_pnl_by_pos[fl["position_id"]] += f(fl["pnl"])
        fill_fee_by_pos[fl["position_id"]] += f(fl["fee"])

    for pos in positions:
        if pos["status"] == "cancelled":
            continue
        pid = pos["id"]
        # position.realized typically equals sum(fill.pnl) ; fees may be separate
        fill_net = fill_pnl_by_pos.get(pid, 0.0)
        # some ledgers put fees inside pnl already
        if fills and abs(fill_net) > 0 and not near(f(pos["realized_pnl"]), fill_net, 0.15):
            # allow fee accounting difference
            if not near(f(pos["realized_pnl"]), fill_net - fill_fee_by_pos.get(pid, 0), 0.15):
                issues.append(
                    f"pos {pos['symbol']}#{pid} realized {f(pos['realized_pnl']):.2f} "
                    f"vs fill.pnl {fill_net:.2f} fees {fill_fee_by_pos.get(pid,0):.2f}"
                )

    print("=== DESK MATH VERIFICATION ===")
    print(f"account mpt={mpt} lev={lev} cash={cash:.2f} realized={acc_realized:.2f}")
    print(
        f"open={len(open_pos)} closed={len(closed_pos)} pending={len(pending_pos)} "
        f"cancelled={len(by_status.get('cancelled', []))}"
    )
    print(f"desk equity={desk_equity:.2f} upnl={desk_upnl:.2f} return={p['totalReturnPct']}%")
    print()
    print(f"OK ({len(ok)}):")
    for line in ok:
        print("  +", line)
    print()
    print(f"ISSUES ({len(issues)}):")
    if not issues:
        print("  (none)")
    for line in issues:
        print("  !", line)

    # Summary JSON for canvas
    out = {
        "ok_count": len(ok),
        "issue_count": len(issues),
        "issues": issues,
        "ok": ok,
        "portfolio": {
            "initial": initial,
            "cash": cash,
            "acc_realized": acc_realized,
            "open_margin": open_margin,
            "closed_realized": closed_realized,
            "open_realized": open_realized,
            "desk_equity": desk_equity,
            "desk_upnl": desk_upnl,
            "total_return_pct": f(p["totalReturnPct"]),
            "win_rate_pct": f(p.get("winRatePct") or 0),
            "mpt": mpt,
        },
        "open": [
            {
                "symbol": x["symbol"],
                "margin_used": f(x["margin_used"]),
                "notional": f(x["notional"]),
                "risk": f(x["risk_amount"]),
                "realized": f(x["realized_pnl"]),
                "rem_qty": f(x["remaining_quantity"]),
                "ini_qty": f(x["initial_quantity"]),
            }
            for x in open_pos
        ],
    }
    Path = __import__("pathlib").Path
    Path("/tmp/desk_math_report.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("\nWrote /tmp/desk_math_report.json")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
