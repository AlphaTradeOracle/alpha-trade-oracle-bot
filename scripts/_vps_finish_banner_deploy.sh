#!/usr/bin/env bash
set -euo pipefail

for i in 1 2 3 4 5 6 7 8 9 10 12 15; do
  if curl -fsS "http://127.0.0.1:8000/api/v1/desk/top-coins?limit=10" -o /tmp/top_coins.json; then
    break
  fi
  sleep 2
done

python3 - <<'PY'
import json
d = json.load(open("/tmp/top_coins.json"))
coins = d.get("coins") or []
syms = [c.get("symbol") for c in coins]
print("top_coins", len(coins), "source", d.get("source"))
print("symbols", " ".join(str(s) for s in syms))
for c in coins:
    print(c.get("rank"), c.get("symbol"), c.get("priceUsd"), c.get("change24hPct"))
assert "USDT" not in syms and "USDC" not in syms, syms
assert len(coins) >= 5, "expected at least 5 top coins"
print("API_OK")
PY

WEB_ROOT=/var/www/alpha-desk
BRANCH=origin/cursor/trading-dashboard-efe9
cd /opt/alpha-trade-oracle-bot
git fetch origin cursor/trading-dashboard-efe9
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
curl -fsS -o /dev/null -w "top=%{http_code}\n" "https://alpha-trade-oracle.com/api/v1/desk/top-coins?limit=10"
echo DONE
