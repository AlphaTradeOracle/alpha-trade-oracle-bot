#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
export PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
PSQL="docker exec -e PGPASSWORD=$PGPASSWORD alpha-trade-oracle-postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A"

docker cp scripts/rescore_signals_with_weights.py alpha-trade-oracle-worker:/app/scripts/

echo "=== BASELINE ==="
$PSQL -c "SELECT status||'|'||COUNT(*)||'|'||COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0) FROM paper_positions GROUP BY status ORDER BY status;"
$PSQL -c "SELECT 'WR|'||COUNT(*) FILTER (WHERE realized_pnl>0)||'|'||COUNT(*) FILTER (WHERE realized_pnl<=0)||'|'||ROUND(100.0*COUNT(*) FILTER (WHERE realized_pnl>0)/NULLIF(COUNT(*),0),1)||'|'||ROUND(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl>0),0)/NULLIF(ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl<=0),0)),0),2) FROM paper_positions WHERE status='closed';"
$PSQL -c "SELECT 'ACCT|'||ROUND(realized_pnl::numeric,2)||'|'||ROUND(cash_balance::numeric,2) FROM paper_accounts WHERE name='default';"

echo "=== RESCORE ==="
docker compose run --rm worker python scripts/rescore_signals_with_weights.py

echo "=== REBUILD ==="
docker compose run --rm worker python -m app.cli paper rebuild --since 2026-07-28T00:00:00+00:00 --all-signals

echo "=== AFTER ==="
$PSQL -c "SELECT status||'|'||COUNT(*)||'|'||COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0) FROM paper_positions GROUP BY status ORDER BY status;"
$PSQL -c "SELECT 'WR|'||COUNT(*) FILTER (WHERE realized_pnl>0)||'|'||COUNT(*) FILTER (WHERE realized_pnl<=0)||'|'||ROUND(100.0*COUNT(*) FILTER (WHERE realized_pnl>0)/NULLIF(COUNT(*),0),1)||'|'||ROUND(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl>0),0)/NULLIF(ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl<=0),0)),0),2) FROM paper_positions WHERE status='closed';"
$PSQL -c "SELECT 'ACCT|'||ROUND(realized_pnl::numeric,2)||'|'||ROUND(cash_balance::numeric,2) FROM paper_accounts WHERE name='default';"
$PSQL -c "SELECT 'TOP|'||symbol||'|'||ROUND(realized_pnl::numeric,2)||'|'||exit_reason FROM paper_positions WHERE status='closed' ORDER BY realized_pnl DESC LIMIT 5;"
$PSQL -c "SELECT 'BOT|'||symbol||'|'||ROUND(realized_pnl::numeric,2)||'|'||exit_reason FROM paper_positions WHERE status='closed' ORDER BY realized_pnl ASC LIMIT 5;"
$PSQL -c "SELECT 'OPEN|'||id||'|'||symbol||'|'||direction||'|'||status||'|'||COALESCE(ROUND(realized_pnl::numeric,2),0) FROM paper_positions WHERE status IN ('open','pending') ORDER BY opened_at DESC;"
