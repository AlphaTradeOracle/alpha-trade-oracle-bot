#!/usr/bin/env bash
set -euo pipefail
echo "== static =="
ls -la /var/www/alpha-desk/index.html
stat -c '%y %n' /var/www/alpha-desk/index.html
echo "== marketRegime in bundle =="
grep -l 'marketRegime\|Market Regime' /var/www/alpha-desk/assets/*.js 2>/dev/null | head -5 || echo 'NOT_IN_BUNDLE'
echo "== live desk =="
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 - <<'PY'
import json,sys
d=json.load(sys.stdin)
p=d.get("portfolio") or {}
print("equity", p.get("equity"))
print("realized", p.get("realizedPnl"))
print("closed", p.get("closedTrades"))
print("open", p.get("openPositions"))
print("has_marketRegime", isinstance(d.get("marketRegime"), dict))
mr=d.get("marketRegime") or {}
print("bias", mr.get("biasLabel"))
print("generatedAt", d.get("generatedAt"))
PY
