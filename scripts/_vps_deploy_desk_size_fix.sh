#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
git fetch origin main
git reset --hard origin/main
docker compose build app
docker compose up -d --no-deps app
sleep 2
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk.json"))
closed=d.get("closedTrades") or d.get("closed") or []
# snapshot shape: may nest under keys
for key in ("closedTrades","closed","trades"):
    pass
# desk snapshot typically has open/pending/closed lists
for key in ("closed","closedTrades","recentClosed"):
    rows=d.get(key)
    if isinstance(rows, list) and rows:
        t=rows[0]
        print("sample", key, {k:t.get(k) for k in ("symbol","positionSize","notional","margin","stop","entry")})
        break
else:
    # try nested
    data=d.get("data") or d
    for key in ("closed","closedTrades"):
        rows=data.get(key) if isinstance(data, dict) else None
        if isinstance(rows, list) and rows:
            t=rows[0]
            print("sample", key, {k:t.get(k) for k in ("symbol","positionSize","notional","margin","stop","entry")})
            break
    else:
        print("keys", sorted(d.keys())[:40])
PY
