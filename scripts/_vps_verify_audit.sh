#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
echo "BOT=$(git rev-parse --short HEAD)"
echo "DASH=$(git rev-parse --short origin/cursor/trading-dashboard-efe9)"

curl -fsS "http://127.0.0.1:8000/api/v1/desk/top-coins?limit=10" -o /tmp/top.json
curl -fsS "http://127.0.0.1:8000/api/v1/desk/snapshot" -o /tmp/desk.json

python3 - <<'PY'
import json
top=json.load(open("/tmp/top.json"))
d=json.load(open("/tmp/desk.json"))
print("TOP", [c["symbol"] for c in top.get("coins") or []])
p=d["portfolio"]
print("realized", p.get("realizedPnl"), "openRealized", p.get("openRealizedPnl"), "account", p.get("accountRealizedPnl"))
print("equityChangePct", p.get("equityChangePct"), "openR", p.get("openR"))
eq=d.get("equity") or []
print("equity_first", eq[0] if eq else None)
opens=[t for t in d["trades"] if t["status"]=="OPEN"]
for t in opens[:3]:
    print(t["symbol"], "stop", t.get("stop"), "cur", t.get("currentStop"), "notional", t.get("notional"), "size", t.get("positionSize"), "realized", t.get("realized"), "r", t.get("r"))
mr=d.get("marketRegime") or {}
print("hardVeto", mr.get("hardVeto"), "scoreBlend", mr.get("scoreBlend"))
PY
