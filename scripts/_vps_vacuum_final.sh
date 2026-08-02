#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"

run_sql() {
  docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
    psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 -c "$1"
}

for t in signals signal_score_components signal_deliveries market_candles; do
  echo "vacuum $t"
  run_sql "VACUUM (ANALYZE) ${t};"
done
echo VACUUM_OK

docker compose exec -T worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
print(
    "strategy",
    s.market_regime_enabled,
    s.institutional_kb_enabled,
    s.institutional_enforce_gates,
    s.signal_short_max_score,
    "digest",
    s.paper_hourly_digest_enabled,
)
PY
curl -fsS -o /dev/null -w "site=%{http_code}\n" https://alpha-trade-oracle.com/
curl -fsS -o /dev/null -w "api=%{http_code}\n" http://127.0.0.1:8000/api/v1/desk/snapshot
ls -1 /var/www/alpha-desk/assets/index-*.js | head -1
echo "bot=$(git rev-parse --short HEAD)"
echo "dash=$(git rev-parse --short origin/cursor/trading-dashboard-efe9)"
