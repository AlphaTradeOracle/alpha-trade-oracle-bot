#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
set -a; . ./.env; set +a
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
SELECT COUNT(*) AS universe FROM assets WHERE in_universe AND is_active;
SELECT timeframe, COUNT(DISTINCT asset_id) AS assets,
       MIN(open_time) AS min_t, MAX(open_time) AS max_t, COUNT(*) AS candles
FROM market_candles GROUP BY 1 ORDER BY 1;
SQL
echo "=== PAPER-ISH ENV ==="
grep -E '^(PAPER_|SIGNAL_|BACKTEST_|MIN_RISK|REGIME_|ATR_|TP_|SCALE_|ENABLE_PAPER)' .env | head -50
