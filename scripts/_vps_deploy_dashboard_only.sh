#!/usr/bin/env bash
# Rebuild/deploy Alpha Desk static site from dashboard branch.
set -euo pipefail

BOT=/opt/alpha-trade-oracle-bot
WEB_ROOT=/var/www/alpha-desk
BRANCH=origin/cursor/trading-dashboard-efe9

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) dashboard deploy start ====="
cd "$BOT"
git fetch origin cursor/trading-dashboard-efe9
echo "dash=$(git rev-parse --short "$BRANCH")"

rm -rf /tmp/alpha-desk-src
mkdir -p /tmp/alpha-desk-src
git archive "$BRANCH" trading-dashboard | tar -x -C /tmp/alpha-desk-src
cd /tmp/alpha-desk-src/trading-dashboard
npm ci
npm run build
rm -rf "${WEB_ROOT:?}/"*
cp -a dist/. "$WEB_ROOT/"
chown -R www-data:www-data "$WEB_ROOT"
nginx -t
systemctl reload nginx

stat -c '%y %n' "$WEB_ROOT/index.html"
ls -1 "$WEB_ROOT"/assets/index-*.js
grep -l 'Aktualisiert\|marketRegime' "$WEB_ROOT"/assets/index-*.js | head -3 || true
curl -fsS -o /dev/null -w "site=%{http_code}\n" https://alpha-trade-oracle.com/
curl -fsS -o /dev/null -w "api=%{http_code}\n" http://127.0.0.1:8000/api/v1/desk/snapshot
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) dashboard deploy done ====="
