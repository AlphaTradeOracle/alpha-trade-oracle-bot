#!/usr/bin/env python3
"""Deep-check desk trades vs DB (run on VPS)."""
from __future__ import annotations

import json
import os
import urllib.request
from collections import Counter, defaultdict

import psycopg

DSN = (
    f"postgresql://alpha_trade_oracle:{os.environ['POSTGRES_PASSWORD']}"
    f"@postgres:5432/alpha_trade_oracle"
)


def f(x) -> float:
    return float(x or 0)


def near(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol


def main() -> None:
    with urllib.request.urlopen("http://app:8000/api/v1/desk/snapshot", timeout=30) as r:
        desk = json.load(r)

    print("keys", sorted(desk.keys()))
    trades = desk.get("trades") or []
    print("trades", len(trades), dict(Counter(t.get("status") for t in trades)))
    p = desk["portfolio"]
    print("portfolio", {k: p.get(k) for k in sorted(p)})

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, symbol, status, entry_price, initial_quantity, remaining_quantity,
                       margin_used, notional, leverage, realized_pnl, risk_amount
                FROM paper_positions
                WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
                  AND status IN ('open','closed','pending')
                """
            )
            cols = [d.name for d in cur.description]
            positions = [dict(zip(cols, row)) for row in cur.fetchall()]

    by_id = {str(t["id"]): t for t in trades}
    issues = []
    ok = []
    open_detail = []
    closed_detail = []

    for pos in positions:
        tid = str(pos["id"])
        t = by_id.get(tid)
        if t is None:
            issues.append(f"missing desk trade id={tid} {pos['symbol']} {pos['status']}")
            continue

        status = pos["status"]
        notional = f(pos["notional"])
        lev = f(pos["leverage"]) or 10.0
        initial_margin = notional / lev
        margin_used = f(pos["margin_used"])
        realized = f(pos["realized_pnl"])
        risk = f(pos["risk_amount"])
        rem = f(pos["remaining_quantity"])
        ini = f(pos["initial_quantity"])
        share = rem / ini if ini else 0

        if status == "open":
            exp_margin = margin_used if margin_used > 0 else initial_margin
            exp_notional = notional * share
            if not near(f(t.get("margin")), exp_margin):
                issues.append(
                    f"{pos['symbol']} margin desk={t.get('margin')} exp={exp_margin}"
                )
            else:
                ok.append(f"{pos['symbol']} margin")
            if not near(f(t.get("notional") or 0), exp_notional, 1.0):
                issues.append(
                    f"{pos['symbol']} notional desk={t.get('notional')} exp={exp_notional:.2f}"
                )
            else:
                ok.append(f"{pos['symbol']} notional")
            if t.get("realized") is not None and not near(f(t["realized"]), realized):
                issues.append(
                    f"{pos['symbol']} open realized desk={t.get('realized')} db={realized}"
                )
            # open R = upnl / (risk * share)
            upnl = f(t.get("upnl"))
            risk_rem = risk * share
            exp_r = upnl / risk_rem if risk_rem else None
            if exp_r is not None and t.get("r") is not None:
                if not near(f(t["r"]), exp_r, 0.02):
                    issues.append(
                        f"{pos['symbol']} openR desk={t.get('r')} exp={exp_r:.2f} "
                        f"(upnl={upnl} risk_rem={risk_rem:.2f})"
                    )
                else:
                    ok.append(f"{pos['symbol']} openR")
            # website Profit% = upnl / margin_out * 100
            profit_pct = upnl / exp_margin * 100 if exp_margin else None
            # NOTE: open R is vs remaining risk; Profit% vs remaining margin.
            # With fixed margin, risk=margin_initial=300, remaining risk = 300*share,
            # remaining margin = margin_used ≈ 300*share → Profit% ≈ openR * 100
            if profit_pct is not None and exp_r is not None:
                if not near(profit_pct, exp_r * 100, 0.5):
                    issues.append(
                        f"{pos['symbol']} Profit%={profit_pct:.2f} vs R*100={exp_r*100:.2f}"
                    )
                else:
                    ok.append(f"{pos['symbol']} Profit%≈R*100")
            open_detail.append(
                {
                    "symbol": pos["symbol"],
                    "margin": exp_margin,
                    "notional": round(exp_notional, 2),
                    "upnl": upnl,
                    "realized_partial": realized,
                    "r": t.get("r"),
                    "profit_pct": None if profit_pct is None else round(profit_pct, 2),
                    "share": round(share, 4),
                }
            )
        elif status == "closed":
            if not near(f(t.get("margin")), initial_margin):
                issues.append(
                    f"CLOSED {pos['symbol']} margin desk={t.get('margin')} exp={initial_margin}"
                )
            else:
                ok.append(f"CLOSED {pos['symbol']} margin")
            if not near(f(t.get("realized")), realized):
                issues.append(
                    f"CLOSED {pos['symbol']} realized desk={t.get('realized')} db={realized}"
                )
            exp_r = realized / risk if risk else None
            if exp_r is not None and t.get("r") is not None:
                if not near(f(t["r"]), exp_r, 0.02):
                    issues.append(
                        f"CLOSED {pos['symbol']} r desk={t.get('r')} exp={exp_r:.2f}"
                    )
                else:
                    ok.append(f"CLOSED {pos['symbol']} r")
            profit_pct = realized / initial_margin * 100 if initial_margin else None
            if profit_pct is not None and exp_r is not None:
                if not near(profit_pct, exp_r * 100, 0.5):
                    issues.append(
                        f"CLOSED {pos['symbol']} Profit%={profit_pct:.2f} vs R*100={exp_r*100:.2f}"
                    )
            closed_detail.append(
                {
                    "symbol": pos["symbol"],
                    "margin": initial_margin,
                    "realized": realized,
                    "r": t.get("r"),
                    "profit_pct": None if profit_pct is None else round(profit_pct, 2),
                }
            )

    # Portfolio openUpnl sum
    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    sum_upnl = sum(f(t.get("upnl")) for t in open_trades)
    if not near(sum_upnl, f(p.get("openUpnl")), 0.05):
        issues.append(f"sum open upnl {sum_upnl:.2f} != portfolio.openUpnl {p.get('openUpnl')}")
    else:
        ok.append(f"openUpnl sum OK ({sum_upnl:.2f})")

    sum_open_r = sum(f(t.get("r")) for t in open_trades if t.get("r") is not None)
    if not near(sum_open_r, f(p.get("openR")), 0.05):
        issues.append(f"sum openR {sum_open_r:.2f} != portfolio.openR {p.get('openR')}")
    else:
        ok.append(f"openR sum OK ({sum_open_r:.2f})")

    report = {
        "ok_count": len(ok),
        "issue_count": len(issues),
        "issues": issues,
        "ok": ok,
        "open_detail": open_detail,
        "closed_detail": closed_detail,
        "portfolio": p,
    }
    print(json.dumps(report, indent=2))
    open("/tmp/desk_trade_check.json", "w", encoding="utf-8").write(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
