#!/usr/bin/env bash
set -euo pipefail
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk.json"))
trades=[t for t in d.get("trades") or [] if str(t.get("symbol","")).upper().startswith("NES")]
for t in trades:
    print({
        "symbol": t.get("symbol"),
        "status": t.get("status"),
        "entry": t.get("entry"),
        "stop": t.get("stop"),
        "exit": t.get("exit"),
        "notional": t.get("notional"),
        "margin": t.get("margin"),
        "positionSize": t.get("positionSize"),
        "leverage": t.get("leverage"),
        "realized": t.get("realized"),
        "r": t.get("r"),
    })
if not trades:
    print("NO_NES")
    # nearest
    for t in d.get("trades") or []:
        if "NES" in str(t.get("symbol","")).upper():
            print("near", t)
PY
