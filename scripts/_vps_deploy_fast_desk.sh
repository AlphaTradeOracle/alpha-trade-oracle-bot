#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== deploy fast desk ====="
git fetch origin main cursor/trading-dashboard-efe9
git reset --hard origin/main

docker compose build app
docker compose up -d --no-deps app

# dashboard
WEB_ROOT=/var/www/alpha-desk
BRANCH=origin/cursor/trading-dashboard-efe9
rm -rf /tmp/alpha-desk-src
mkdir -p /tmp/alpha-desk-src
git archive "$BRANCH" trading-dashboard | tar -x -C /tmp/alpha-desk-src
cd /tmp/alpha-desk-src/trading-dashboard
npm ci --silent
npm run build
rm -rf "${WEB_ROOT:?}/"*
cp -a dist/. "$WEB_ROOT/"
chown -R www-data:www-data "$WEB_ROOT"
nginx -t && systemctl reload nginx
cd /opt/alpha-trade-oracle-bot

sleep 2
echo "==> latency cold"
curl -o /dev/null -s -w 'ttfb=%{time_starttransfer} total=%{time_total} code=%{http_code}\n' \
  http://127.0.0.1:8000/api/v1/desk/snapshot
echo "==> latency warm1"
curl -o /dev/null -s -w 'ttfb=%{time_starttransfer} total=%{time_total} code=%{http_code}\n' \
  http://127.0.0.1:8000/api/v1/desk/snapshot
echo "==> latency warm2"
curl -o /dev/null -s -w 'ttfb=%{time_starttransfer} total=%{time_total} code=%{http_code}\n' \
  http://127.0.0.1:8000/api/v1/desk/snapshot

echo "bot=$(git rev-parse --short HEAD)"
echo "dash=$(git rev-parse --short origin/cursor/trading-dashboard-efe9)"
echo DONE
