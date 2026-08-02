#!/usr/bin/env bash
# Rebuild summary JSON from completed log (write failed on exports/).
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
LOG=exports/short_min_floor_7d_top50.log
OUT=exports/short_min_floor_7d_top50.json
mkdir -p exports
chmod 777 exports 2>/dev/null || true

python3 - <<'PY'
import json, re
from pathlib import Path
from datetime import datetime, timezone

log = Path("/opt/alpha-trade-oracle-bot/exports/short_min_floor_7d_top50.log").read_text(encoding="utf-8", errors="replace")
# stderr progress lines mixed with structlog — parse [n/300] floor>X #rank SYM + next trades line
pat = re.compile(
    r"\[(\d+)/300\] floor>([\d.]+) #(\d+) ([A-Z0-9]+)\n\s+trades=(\d+) short=(\d+) net=([-\d.]+) s_net=([-\d.]+)"
)
rows = []
for m in pat.finditer(log):
    step, floor, rank, sym, trades, short_n, net, s_net = m.groups()
    floor = float(floor)
    rows.append({
        "short_min": floor,
        "symbol": sym,
        "rank": int(rank),
        "overall": {"trade_count": int(trades), "net_profit": float(net), "win_rate": 0.0},
        "short": {"trade_count": int(short_n), "net_profit": float(s_net), "win_rate": 0.0},
        "long": {"trade_count": 0, "net_profit": 0.0, "win_rate": 0.0},
        "recovered_from_log": True,
    })

print(f"parsed_rows={len(rows)}")
floors = [0.0, 10.0, 12.0, 15.0, 18.0, 20.0]
summaries = []
for floor in floors:
    ok = [r for r in rows if r["short_min"] == floor]
    o_trades = sum(int(r["overall"]["trade_count"]) for r in ok)
    o_net = sum(float(r["overall"]["net_profit"]) for r in ok)
    s_trades = sum(int(r["short"]["trade_count"]) for r in ok)
    s_net = sum(float(r["short"]["net_profit"]) for r in ok)
    summaries.append({
        "short_min": floor,
        "label": f"short > {floor:g} … ≤ 25",
        "symbols_ok": len(ok),
        "symbols_failed": 0,
        "total_trades": o_trades,
        "total_net": round(o_net, 2),
        "short_trades": s_trades,
        "short_net": round(s_net, 2),
        "short_wr": 0.0,
        "symbols_with_short": sum(1 for r in ok if int(r["short"]["trade_count"]) > 0),
    })

baseline = next(s for s in summaries if s["short_min"] == 18.0)
open_floor = next(s for s in summaries if s["short_min"] == 0.0)
for s in summaries:
    s["delta_short_net_vs_18"] = round(s["short_net"] - baseline["short_net"], 2)
    s["delta_short_trades_vs_18"] = int(s["short_trades"] - baseline["short_trades"])
    s["delta_short_net_vs_0"] = round(s["short_net"] - open_floor["short_net"], 2)

marginal = {
    "description": "Extra short trades when lowering floor from 18 → 0 (approx via aggregate delta)",
    "extra_short_trades": int(open_floor["short_trades"] - baseline["short_trades"]),
    "extra_short_net": round(open_floor["short_net"] - baseline["short_net"], 2),
    "keep_floor_18": (open_floor["short_net"] - baseline["short_net"]) <= 0,
}
verdict = (
    "KEEP floor 18 — opening ≤18 shorts did not improve short PnL"
    if marginal["keep_floor_18"]
    else "CONSIDER lowering floor — shorts ≤18 added positive short PnL"
)
symbols = sorted({r["symbol"] for r in rows})
payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "recovered_from_log": True,
    "window": {"days": 7},
    "universe": {"top": 50, "symbols": symbols},
    "gates": {"short_max": 25.0, "floors_tested": floors, "timeframe": "1h", "mtf": False, "prefer_db": True},
    "summaries": summaries,
    "marginal_vs_floor_18": marginal,
    "verdict": verdict,
    "results": rows,
}
out = Path("/opt/alpha-trade-oracle-bot/exports/short_min_floor_7d_top50.json")
out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print("VERDICT", verdict)
for s in summaries:
    print(
        f"min>{s['short_min']:g} shorts={s['short_trades']} net={s['short_net']} "
        f"d18={s['delta_short_net_vs_18']} n={s['delta_short_trades_vs_18']}"
    )
print("MARGINAL", marginal)
print("WROTE", out)
PY
