#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
LOG=exports/paper_rebuild_universe_only.log
mkdir -p exports scripts

SINCE="2026-07-31T16:32:35+00:00"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) start =====" | tee -a "$LOG"
echo "SINCE=$SINCE" | tee -a "$LOG"

echo "----- margin-fix markers in worker -----" | tee -a "$LOG"
docker exec alpha-trade-oracle-worker grep -n "remaining_before\|cash_needed" \
  /app/app/services/paper_trading_service.py | head -20 | tee -a "$LOG"

echo "----- before -----" | tee -a "$LOG"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT status, COUNT(*) FROM paper_positions GROUP BY 1 ORDER BY 1;" | tee -a "$LOG"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT cash_balance, realized_pnl, initial_balance FROM paper_accounts LIMIT 1;" | tee -a "$LOG"

echo "----- sync rebuild script into worker -----" | tee -a "$LOG"
docker cp scripts/paper_rebuild_universe_only.py \
  alpha-trade-oracle-worker:/app/scripts/paper_rebuild_universe_only.py

echo "----- rebuild universe-only -----" | tee -a "$LOG"
docker compose exec -T worker python scripts/paper_rebuild_universe_only.py --since "$SINCE" \
  2>&1 | tee -a "$LOG"

echo "----- after -----" | tee -a "$LOG"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT status, COUNT(*) FROM paper_positions GROUP BY 1 ORDER BY 1;" | tee -a "$LOG"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT cash_balance, realized_pnl, initial_balance FROM paper_accounts LIMIT 1;" | tee -a "$LOG"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(DISTINCT symbol) AS symbols,
          COALESCE(SUM(CASE WHEN status='closed' THEN realized_pnl ELSE 0 END),0) AS closed_pnl,
          COALESCE(SUM(CASE WHEN status='open' THEN margin_used ELSE 0 END),0) AS open_margin
   FROM paper_positions;" | tee -a "$LOG"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) done =====" | tee -a "$LOG"
