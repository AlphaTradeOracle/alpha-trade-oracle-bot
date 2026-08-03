#!/bin/bash
set -euo pipefail
chmod -R a+rX /var/www/alpha-desk
chown -R www-data:www-data /var/www/alpha-desk
ls -la /var/www/alpha-desk/assets/ | head -10
JS=$(basename "$(ls /var/www/alpha-desk/assets/index-*.js | head -1)")
curl -fsS -o /dev/null -w "js=%{http_code} file=$JS\n" "https://alpha-trade-oracle.com/assets/$JS"
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_snap.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk_snap.json"))
p=d["portfolio"]
for k in [
    "equity","accountRealizedPnl","realizedPnl","openRealizedPnl","winRatePct",
    "openPositions","closedTrades","openUpnl","totalReturnPct","cash","marginLocked",
]:
    print(k, p.get(k))
mr=d.get("marketRegime") or {}
print("regime_bias", mr.get("bias"), "global", mr.get("globalScore"), "fg", mr.get("fearGreed"))
print("trades", len(d.get("trades") or []))
print("equity_points", len(d.get("equity") or []))
PY
