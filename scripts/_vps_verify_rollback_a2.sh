#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot

echo "=== account ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT name, initial_balance, cash_balance, realized_pnl FROM paper_accounts WHERE name='default';"

echo "=== cli paper status ==="
docker compose exec -T worker python -m app.cli paper status 2>&1 | tail -50

echo "=== recent closed (last 10) ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT symbol, direction, status, opened_at::date, round(realized_pnl::numeric,2) AS pnl
   FROM paper_positions
   WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
     AND status='closed'
   ORDER BY closed_at DESC NULLS LAST
   LIMIT 10;"

echo "=== key symbols closed ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT symbol, count(*) n, round(sum(realized_pnl)::numeric,2) pnl
   FROM paper_positions
   WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
     AND status='closed'
     AND symbol IN ('CVCUSDT','TREEUSDT','PHAUSDT','QNTUSDT','REDUSDT','BATUSDT','MOCAUSDT','APTUSDT','DEEPUSDT')
   GROUP BY 1 ORDER BY 1;"

echo "=== DONE ==="
