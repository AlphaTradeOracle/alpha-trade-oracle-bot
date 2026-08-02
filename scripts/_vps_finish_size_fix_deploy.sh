#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk.json; then
    break
  fi
  sleep 2
done

python3 - <<'PY'
import json
d=json.load(open("/tmp/desk.json"))
closed=[t for t in d.get("trades") or [] if t.get("status")=="CLOSED"]
npc=[t for t in closed if t.get("symbol")=="NPCUSDT"]
t=(npc or closed)[0]
print("API", {k:t.get(k) for k in ("symbol","positionSize","notional","margin","stop","entry","leverage")})
assert t.get("notional") and t["notional"] > 0
assert t.get("margin") and t["margin"] > 0
print("API_OK")
PY

bash /tmp/_vps_deploy_dashboard_only.sh
echo DONE
