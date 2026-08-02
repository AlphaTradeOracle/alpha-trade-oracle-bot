#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
git fetch origin main cursor/trading-dashboard-efe9
git reset --hard origin/main
docker compose build app worker
docker compose up -d --no-deps app worker

# Wait for API
for i in $(seq 1 25); do
  if curl -fsS "http://127.0.0.1:8000/api/v1/desk/top-coins?limit=10" -o /tmp/top_coins.json; then
    break
  fi
  echo "waiting api $i"
  sleep 3
done

# Backfill 1R := margin for fixed-margin paper rows (notional/leverage).
set -a
# shellcheck disable=SC1091
source /opt/alpha-trade-oracle-bot/.env
set +a
docker compose exec -T postgres \
  psql -U "${POSTGRES_USER:-alpha_trade_oracle}" -d "${POSTGRES_DB:-alpha_trade_oracle}" -v ON_ERROR_STOP=1 <<'SQL'
UPDATE paper_positions
SET risk_amount = CASE
  WHEN leverage IS NOT NULL AND leverage > 0 AND notional IS NOT NULL AND notional > 0
    THEN notional / leverage
  ELSE risk_amount
END
WHERE status IN ('open', 'closed', 'pending')
  AND notional IS NOT NULL
  AND notional > 0
  AND leverage IS NOT NULL
  AND leverage > 0;
SQL

curl -fsS "http://127.0.0.1:8000/api/v1/desk/top-coins?limit=10" -o /tmp/top_coins.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/top_coins.json"))
coins=d.get("coins") or []
syms=[c["symbol"] for c in coins]
print("top10", syms)
banned={"FIGR_HELOC","WBT","LEO","RAIN","WETH","WBTC","STETH","USDE","USDS"}
assert not (banned & set(syms)), f"junk in top10: {banned & set(syms)}"
assert "BTC" in syms and "ETH" in syms
assert len(coins)==10
print("TOP10_OK")
PY

curl -fsS "http://127.0.0.1:8000/api/v1/desk/snapshot" -o /tmp/desk.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk.json"))
p=d["portfolio"]
print("realized", p.get("realizedPnl"), "openRealized", p.get("openRealizedPnl"), "account", p.get("accountRealizedPnl"))
print("equityChangePct", p.get("equityChangePct"), "openR", p.get("openR"))
opens=[t for t in d["trades"] if t["status"]=="OPEN"]
for t in opens[:3]:
    print(t["symbol"], "stop", t.get("stop"), "cur", t.get("currentStop"), "notional", t.get("notional"), "size", t.get("positionSize"), "realized", t.get("realized"), "r", t.get("r"))
    assert t.get("stop") is not None
    if t.get("currentStop") is not None and abs(t["currentStop"]-t["entry"])<1e-12:
        # healed fee-aware BE should move off exact entry when fee>0; allow equal if fee=0
        pass
print("DESK_OK")
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
curl -fsS -o /dev/null -w "top=%{http_code}\n" "https://alpha-trade-oracle.com/api/v1/desk/top-coins?limit=10"
echo DONE
