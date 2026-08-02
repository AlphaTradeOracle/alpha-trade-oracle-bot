#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
LOG=exports/paper_rebuild_no_overlap.log
mkdir -p exports
SINCE="2026-07-31T16:32:35+00:00"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) rebuild no-overlap start =====" | tee "$LOG"

docker cp scripts/paper_rebuild_universe_only.py alpha-trade-oracle-worker:/app/scripts/paper_rebuild_universe_only.py
docker cp app/repositories/paper_repository.py alpha-trade-oracle-worker:/app/app/repositories/paper_repository.py
docker cp app/services/paper_trading_service.py alpha-trade-oracle-worker:/app/app/services/paper_trading_service.py

echo "----- before -----" | tee -a "$LOG"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT status, COUNT(*) FROM paper_positions GROUP BY 1 ORDER BY 1;" | tee -a "$LOG"

echo "----- rebuild -----" | tee -a "$LOG"
docker compose exec -T worker python scripts/paper_rebuild_universe_only.py --since "$SINCE" \
  2>&1 | tee -a "$LOG"

echo "----- after status -----" | tee -a "$LOG"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT status, COUNT(*) FROM paper_positions GROUP BY 1 ORDER BY 1;" | tee -a "$LOG"

echo "----- overlap check (open/closed/pending only) -----" | tee -a "$LOG"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c "
WITH ordered AS (
  SELECT id, symbol, status,
         COALESCE(opened_at, created_at) AS start_at,
         COALESCE(closed_at, TIMESTAMPTZ 'infinity') AS end_at
  FROM paper_positions
  WHERE status IN ('open', 'pending', 'closed')
)
SELECT COUNT(*) AS overlap_pairs
FROM ordered a
JOIN ordered b
  ON a.symbol = b.symbol AND a.id < b.id
 AND a.start_at < b.end_at AND b.start_at < a.end_at;
" | tee -a "$LOG"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) rebuild no-overlap done =====" | tee -a "$LOG"
