#!/usr/bin/env bash
# Backfill paper trades from signals since ledger start for current Top-400 universe.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
LOG=exports/paper_backfill_top400.log
mkdir -p exports
exec > >(tee -a "$LOG") 2>&1

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) paper backfill top400 start ====="

SINCE=$(docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A -c \
  "SELECT COALESCE(MIN(opened_at)::text, (NOW() - INTERVAL '3 days')::text) FROM paper_positions;")
SINCE=$(echo "$SINCE" | tr -d '[:space:]')
echo "SINCE=$SINCE"

echo "----- before -----"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT status, COUNT(*) FROM paper_positions GROUP BY status ORDER BY 1;"

echo "----- backfill -----"
docker compose exec -T worker python -m app.cli paper backfill \
  --since "$SINCE" \
  --all-qualifying \
  --update

echo "----- after -----"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT status, COUNT(*) FROM paper_positions GROUP BY status ORDER BY 1;"

docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(DISTINCT symbol) AS symbols_with_paper FROM paper_positions;"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) paper backfill top400 done ====="
