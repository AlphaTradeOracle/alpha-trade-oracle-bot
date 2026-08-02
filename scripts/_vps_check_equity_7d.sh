#!/usr/bin/env bash
set -euo pipefail
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk.json
python3 - <<'PY'
import json
from datetime import datetime, timezone, timedelta
d=json.load(open("/tmp/desk.json"))
eq=d.get("equity") or []
p=d.get("portfolio") or {}
print("portfolio", {k:p.get(k) for k in ("equity","cash","realizedPnl","totalReturnPct","totalCapital","winRatePct")})
print("equity_points", len(eq))
if not eq:
    raise SystemExit(0)
# parse times
pts=[]
for e in eq:
    t=e.get("t")
    try:
        if isinstance(t,(int,float)):
            dt=datetime.fromtimestamp(t, tz=timezone.utc)
        else:
            dt=datetime.fromisoformat(str(t).replace("Z","+00:00"))
    except Exception as ex:
        continue
    pts.append((dt, float(e.get("equity",0))))
pts.sort()
print("first", pts[0][0].isoformat(), pts[0][1])
print("last", pts[-1][0].isoformat(), pts[-1][1])
now=pts[-1][0]
cut=now-timedelta(days=7)
win=[x for x in pts if x[0]>=cut]
print("7d_points", len(win))
if win:
    base=win[0][1]
    last=win[-1][1]
    pct=((last-base)/base*100) if base else 0
    print("7d_start", win[0][0].isoformat(), base)
    print("7d_end", win[-1][0].isoformat(), last)
    print("7d_return_pct", round(pct,2))
    print("7d_delta_usd", round(last-base,2))
# also check if curve only starts at paper reset
print("span_days", round((pts[-1][0]-pts[0][0]).total_seconds()/86400,2))
PY
