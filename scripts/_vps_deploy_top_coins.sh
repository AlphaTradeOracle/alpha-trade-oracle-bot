#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
git fetch origin main cursor/trading-dashboard-efe9
git reset --hard origin/main
docker compose build app
docker compose up -d --no-deps app
sleep 3
curl -fsS "http://127.0.0.1:8000/api/v1/desk/top-coins?limit=10" -o /tmp/top_coins.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/top_coins.json"))
coins=d.get("coins") or []
print("top_coins", len(coins), "source", d.get("source"))
for c in coins[:3]:
    print(c.get("rank"), c.get("symbol"), c.get("priceUsd"), c.get("change24hPct"), "spark", len(c.get("sparkline") or []))
assert len(coins) >= 5, "expected at least 5 top coins"
assert all(c.get("priceUsd") for c in coins), "missing prices"
print("API_OK")
PY

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
echo "bot=$(git -C /opt/alpha-trade-oracle-bot rev-parse --short HEAD)"
echo "dash=$(git -C /opt/alpha-trade-oracle-bot rev-parse --short origin/cursor/trading-dashboard-efe9)"
curl -fsS -o /dev/null -w "site=%{http_code}\n" https://alpha-trade-oracle.com/
curl -fsS -o /dev/null -w "top=%{http_code}\n" https://alpha-trade-oracle.com/api/v1/desk/top-coins?limit=10
echo DONE
