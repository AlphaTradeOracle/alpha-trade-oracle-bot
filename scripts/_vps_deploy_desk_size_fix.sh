#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
git fetch origin main cursor/trading-dashboard-efe9
git reset --hard origin/main
docker compose build app
docker compose up -d --no-deps app
sleep 2
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk.json"))
closed=[t for t in d.get("trades") or [] if t.get("status")=="CLOSED"]
npc=[t for t in closed if t.get("symbol")=="NPCUSDT"]
sample=(npc or closed)[:1]
for t in sample:
    print({k:t.get(k) for k in ("symbol","status","positionSize","notional","margin","stop","entry","leverage")})
if not sample:
    print("no_closed")
PY

# dashboard static
WEB_ROOT=/var/www/alpha-desk
BRANCH=origin/cursor/trading-dashboard-efe9
rm -rf /tmp/alpha-desk-src
mkdir -p /tmp/alpha-desk-src
git archive "$BRANCH" trading-dashboard | tar -x -C /tmp/alpha-desk-src
cd /tmp/alpha-desk-src/trading-dashboard
npm ci
npm run build
rm -rf "${WEB_ROOT:?}/"*
cp -a dist/. "$WEB_ROOT/"
chown -R www-data:www-data "$WEB_ROOT"
nginx -t && systemctl reload nginx
echo "dash=$(git -C /opt/alpha-trade-oracle-bot rev-parse --short origin/cursor/trading-dashboard-efe9)"
curl -fsS -o /dev/null -w "site=%{http_code}\n" https://alpha-trade-oracle.com/
echo DONE
