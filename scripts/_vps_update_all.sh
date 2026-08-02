#!/usr/bin/env bash
# Sync bot main + rebuild/deploy Alpha Desk dashboard.
set -euo pipefail

BOT=/opt/alpha-trade-oracle-bot
WEB_ROOT=/var/www/alpha-desk
BRANCH=origin/cursor/trading-dashboard-efe9

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) update-all start ====="

cd "$BOT"
git fetch origin main cursor/trading-dashboard-efe9
git reset --hard origin/main

upsert_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}
upsert_env MARKET_REGIME_ENABLED true
upsert_env MARKET_REGIME_HARD_VETO true
upsert_env INSTITUTIONAL_KB_ENABLED true
upsert_env INSTITUTIONAL_ENFORCE_GATES false
upsert_env SIGNAL_SHORT_MAX_SCORE 25
upsert_env PAPER_HOURLY_DIGEST_ENABLED false

echo "==> bot build/restart"
docker compose build migrate app worker
docker compose run --rm migrate
docker compose up -d --no-deps app worker

echo "==> dashboard build"
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

echo "==> verify"
cd "$BOT"
echo "bot=$(git rev-parse --short HEAD)"
curl -fsS http://127.0.0.1:8000/health
echo
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk.json"))
p=d.get("portfolio") or {}
mr=d.get("marketRegime") or {}
print("equity", p.get("equity"), "realized", p.get("realizedPnl"), "closed", p.get("closedTrades"))
print("marketRegime", mr.get("biasLabel"), "available", mr.get("available"))
PY
stat -c '%y %n' "$WEB_ROOT/index.html"
grep -l 'marketRegime\|Market Regime' "$WEB_ROOT"/assets/*.js | head -3 || echo 'WARN: marketRegime not in bundle'
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) update-all done ====="
