#!/bin/bash
set -eu
cd /opt/alpha-trade-oracle-bot
export PGPASSWORD=$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT status, COALESCE(exit_reason, '-') AS reason, COUNT(*)
FROM paper_positions GROUP BY 1,2 ORDER BY 1,2;

SELECT COUNT(*) AS candles FROM market_candles;
SELECT COUNT(DISTINCT asset_id || ':' || timeframe) AS series FROM market_candles;
SELECT MIN(opened_at) AS first_open, MAX(opened_at) AS last_open, MAX(closed_at) AS last_close
FROM paper_positions;
SELECT COUNT(DISTINCT symbol) AS symbols FROM paper_positions;
SELECT COUNT(*) AS fills FROM paper_fills;
SQL
