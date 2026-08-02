#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
git fetch origin main cursor/trading-dashboard-efe9
echo "bot_local=$(git rev-parse --short HEAD)"
echo "bot_main=$(git rev-parse --short origin/main)"
echo "dash=$(git rev-parse --short origin/cursor/trading-dashboard-efe9)"
stat -c 'static=%y' /var/www/alpha-desk/index.html
BUNDLE=$(ls -1 /var/www/alpha-desk/assets/index-*.js | head -1)
echo "bundle=$BUNDLE"
if grep -q 'winRatePct' "$BUNDLE"; then echo "bundle_HAS_winRatePct"; else echo "bundle_MISSING_winRatePct"; fi
if grep -q 'WR ' "$BUNDLE"; then echo "bundle_HAS_WR_label"; else echo "bundle_MISSING_WR_label"; fi
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk.json
python3 - <<'PY'
import json
p=json.load(open("/tmp/desk.json"))["portfolio"]
print("api winRatePct", p.get("winRatePct"), "openR", p.get("openR"), "closed", p.get("closedTrades"))
PY
