#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

# Run verification inside worker (has app deps + network to postgres/api).
docker compose exec -T -e POSTGRES_PASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)" worker python - <<'PY'
from __future__ import annotations

import json
import os
import urllib.request
from collections import defaultdict

import psycopg

DESK_URL = "http://app:8000/api/v1/desk/snapshot"
# fallback localhost via host gateway if app hostname fails
DSN = (
    f"postgresql://alpha_trade_oracle:{os.environ['POSTGRES_PASSWORD']}"
    f"@postgres:5432/alpha_trade_oracle"
)


def f(x) -> float:
    return float(x or 0)


def near(a: float, b: float, tol: float = 0.02) -> bool:
    return abs(a - b) <= tol


issues: list[str] = []
ok: list[str] = []

desk = None
for url in (DESK_URL, "http://127.0.0.1:8000/api/v1/desk/snapshot"):
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            desk = json.load(resp)
            break
    except Exception as exc:
        last = exc
else:
    raise SystemExit(f"desk fetch failed: {last}")

p = desk["portfolio"]
open_trades = desk.get("openTrades") or []
closed_trades = desk.get("closedTrades") or []
pending_trades = desk.get("pendingTrades") or []

with psycopg.connect(DSN) as conn:
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
                   realized_pnl, fees, risk_amount
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
            SELECT f.position_id, f.reason, f.quantity, f.fee, f.pnl
            FROM paper_fills f
            JOIN paper_positions p ON p.id = f.position_id
            WHERE p.account_id=%s
            """,
            (acc_id,),
        )
        fcols = [d.name for d in cur.description]
        fills = [dict(zip(fcols, row)) for row in cur.fetchall()]

by_status: dict[str, list] = defaultdict(list)
for pos in positions:
    by_status[pos["status"]].append(pos)

open_pos = by_status.get("open", [])
closed_pos = by_status.get("closed", [])
pending_pos = by_status.get("pending", [])

open_margin = sum(f(x["margin_used"]) for x in open_pos)
closed_realized = sum(f(x["realized_pnl"]) for x in closed_pos)
open_realized = sum(f(x["realized_pnl"]) for x in open_pos)
sum_all_realized = sum(f(x["realized_pnl"]) for x in positions)

lhs = cash + open_margin
rhs = initial + acc_realized
(ok if near(lhs, rhs, 0.05) else issues).append(
    f"invariant cash+margin={lhs:.2f} vs initial+realized={rhs:.2f}"
    if not near(lhs, rhs, 0.05)
    else f"cash+open_margin == initial+realized ({lhs:.2f})"
)

(ok if near(acc_realized, sum_all_realized, 0.05) else issues).append(
    f"account.realized {acc_realized:.2f} != sum positions {sum_all_realized:.2f}"
    if not near(acc_realized, sum_all_realized, 0.05)
    else f"account.realized == sum(position.realized) ({acc_realized:.2f})"
)

(ok if near(mpt, 300, 0.01) else issues).append(
    f"margin_per_trade={mpt} expected 300" if not near(mpt, 300, 0.01) else "margin_per_trade=300"
)

for name, got, exp, tol in [
    ("totalCapital", f(p["totalCapital"]), initial, 0.05),
    ("cash", f(p["cash"]), cash, 0.05),
    ("marginLocked", f(p["marginLocked"]), open_margin, 0.05),
    ("realizedPnl_closed", f(p["realizedPnl"]), closed_realized, 0.05),
    ("openRealizedPnl", f(p.get("openRealizedPnl") or 0), open_realized, 0.05),
    ("accountRealizedPnl", f(p.get("accountRealizedPnl") or 0), acc_realized, 0.05),
    ("openPositions", f(p["openPositions"]), float(len(open_pos)), 0),
    ("pendingOrders", f(p["pendingOrders"]), float(len(pending_pos)), 0),
    ("closedTrades", f(p["closedTrades"]), float(len(closed_pos)), 0),
]:
    if near(got, exp, tol):
        ok.append(f"desk.{name}={got}")
    else:
        issues.append(f"desk.{name}={got} expected {exp}")

desk_equity = f(p["equity"])
desk_upnl = f(p["openUpnl"])
recon = cash + open_margin + desk_upnl
if near(desk_equity, recon, 0.5):
    ok.append(f"equity≈cash+margin+upnl ({desk_equity:.2f})")
else:
    issues.append(
        f"equity {desk_equity:.2f} vs cash+margin+upnl {recon:.2f} "
        f"(cash={cash:.2f} margin={open_margin:.2f} upnl={desk_upnl:.2f})"
    )

total_return = ((desk_equity - initial) / initial) * 100 if initial else 0
if near(f(p["totalReturnPct"]), total_return, 0.05):
    ok.append(f"totalReturnPct={p['totalReturnPct']}")
else:
    issues.append(f"totalReturnPct {p['totalReturnPct']} vs {total_return:.2f}")

wins = sum(1 for x in closed_pos if f(x["realized_pnl"]) > 0)
wr = (wins / len(closed_pos) * 100) if closed_pos else 0.0
if near(f(p.get("winRatePct") or 0), wr, 0.15):
    ok.append(f"winRatePct={p.get('winRatePct')} ({wins}/{len(closed_pos)})")
else:
    issues.append(f"winRatePct {p.get('winRatePct')} vs {wr:.1f} ({wins}/{len(closed_pos)})")

# Desk lists counts
if len(open_trades) != len(open_pos):
    issues.append(f"openTrades len {len(open_trades)} != db {len(open_pos)}")
else:
    ok.append(f"openTrades count {len(open_trades)}")
if len(closed_trades) != len(closed_pos):
    issues.append(f"closedTrades len {len(closed_trades)} != db {len(closed_pos)}")
else:
    ok.append(f"closedTrades count {len(closed_trades)}")

# Per open trade mapping
desk_open = {t["symbol"]: t for t in open_trades}
open_rows = []
for pos in open_pos:
    sym = pos["symbol"]
    t = desk_open.get(sym)
    row = {
        "symbol": sym,
        "db_margin": f(pos["margin_used"]),
        "db_notional": f(pos["notional"]),
        "db_risk": f(pos["risk_amount"]),
        "db_realized": f(pos["realized_pnl"]),
        "share": f(pos["remaining_quantity"]) / f(pos["initial_quantity"])
        if f(pos["initial_quantity"])
        else 0,
    }
    if not t:
        issues.append(f"OPEN {sym} missing on desk")
        open_rows.append(row)
        continue
    row.update(
        {
            "desk_margin": f(t.get("margin")),
            "desk_notional": f(t.get("notional")),
            "desk_upnl": f(t.get("upnl")),
            "desk_realized": f(t.get("realized")),
            "desk_r": f(t.get("r")),
        }
    )
    if not near(f(t.get("margin") or 0), f(pos["margin_used"]), 0.05):
        issues.append(f"{sym} margin desk={t.get('margin')} db={pos['margin_used']}")
    else:
        ok.append(f"{sym} margin OK ({t.get('margin')})")

    if not near(f(pos["risk_amount"]), 300, 0.05):
        issues.append(f"{sym} risk={pos['risk_amount']} expected 300")
    if not near(f(pos["notional"]), 3000, 1.0):
        issues.append(f"{sym} notional={pos['notional']} expected 3000")

    # Profit% website = upnl/margin*100
    if f(t.get("margin") or 0) > 0 and t.get("upnl") is not None:
        pct = f(t["upnl"]) / f(t["margin"]) * 100
        row["profit_pct"] = round(pct, 2)
        ok.append(f"{sym} Profit%={pct:+.2f}%")

    # open R: realized+upnl vs risk * share? desk uses map_position logic
    if t.get("r") is not None and f(pos["risk_amount"]) > 0:
        # For OPEN, r is typically (realized_partials + upnl) / risk  or upnl-based
        pass
    open_rows.append(row)

# Closed: r = realized/risk, profit% = realized/margin, margin=notional/lev
closed_rows = []
for pos in closed_pos:
    notional = f(pos["notional"])
    lev_p = f(pos["leverage"]) or lev
    margin = notional / lev_p if lev_p else 0
    realized = f(pos["realized_pnl"])
    risk = f(pos["risk_amount"])
    exp_r = realized / risk if risk else None
    pct = realized / margin * 100 if margin else None
    # find desk trade
    matches = [
        x
        for x in closed_trades
        if x.get("symbol") == pos["symbol"]
        and near(f(x.get("realized")), realized, 0.05)
    ]
    t = matches[0] if matches else None
    row = {
        "symbol": pos["symbol"],
        "realized": realized,
        "margin": margin,
        "risk": risk,
        "profit_pct": None if pct is None else round(pct, 2),
        "r": None if exp_r is None else round(exp_r, 2),
    }
    if t:
        if not near(f(t.get("margin") or 0), margin, 0.05):
            issues.append(
                f"CLOSED {pos['symbol']} margin desk={t.get('margin')} exp={margin:.2f}"
            )
        if t.get("r") is not None and exp_r is not None and not near(f(t["r"]), exp_r, 0.02):
            issues.append(f"CLOSED {pos['symbol']} r desk={t['r']} exp={exp_r:.2f}")
        else:
            ok.append(f"CLOSED {pos['symbol']} r/margin OK")
        row["desk_r"] = t.get("r")
        row["desk_margin"] = t.get("margin")
    closed_rows.append(row)

# Fill vs position realized
fill_pnl = defaultdict(float)
for fl in fills:
    fill_pnl[fl["position_id"]] += f(fl["pnl"])
for pos in positions:
    if pos["status"] == "cancelled":
        continue
    if pos["id"] not in fill_pnl:
        continue
    if not near(f(pos["realized_pnl"]), fill_pnl[pos["id"]], 0.2):
        issues.append(
            f"fills≠position {pos['symbol']}: pos={f(pos['realized_pnl']):.2f} "
            f"fills={fill_pnl[pos['id']]:.2f}"
        )

# Website KPI total realized
kpi = f(p.get("accountRealizedPnl") or 0)
if near(kpi, acc_realized, 0.05):
    ok.append(f"website Realized KPI total={kpi:.2f}")
else:
    issues.append(f"website Realized KPI {kpi} != account {acc_realized}")

report = {
    "ok_count": len(ok),
    "issue_count": len(issues),
    "issues": issues,
    "ok": ok,
    "portfolio": {
        "initial": initial,
        "cash": cash,
        "acc_realized": acc_realized,
        "closed_realized": closed_realized,
        "open_realized": open_realized,
        "open_margin": open_margin,
        "desk_equity": desk_equity,
        "desk_upnl": desk_upnl,
        "total_return_pct": f(p["totalReturnPct"]),
        "win_rate_pct": f(p.get("winRatePct") or 0),
        "mpt": mpt,
        "kpi_total_realized": kpi,
    },
    "open": open_rows,
    "closed": closed_rows,
}
print(json.dumps(report, indent=2))
open("/tmp/desk_math_report.json", "w", encoding="utf-8").write(
    json.dumps(report, indent=2)
)
raise SystemExit(1 if issues else 0)
PY

echo "exit=$?"
cp -f /tmp/desk_math_report.json /opt/alpha-trade-oracle-bot/exports/desk_math_report.json 2>/dev/null || \
  docker compose cp worker:/tmp/desk_math_report.json /tmp/desk_math_report.json
ls -la /tmp/desk_math_report.json 2>/dev/null || true
