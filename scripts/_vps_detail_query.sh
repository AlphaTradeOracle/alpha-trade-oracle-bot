#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
export PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
PSQL="docker exec -e PGPASSWORD=$PGPASSWORD alpha-trade-oracle-postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A"

$PSQL -c "SELECT exit_reason||'|'||COUNT(*)||'|'||ROUND(COALESCE(SUM(realized_pnl),0)::numeric,2) FROM paper_positions WHERE status='closed' GROUP BY exit_reason ORDER BY SUM(realized_pnl) DESC;"
$PSQL -c "SELECT id||'|'||symbol||'|'||direction||'|'||ROUND(entry_price::numeric,8)||'|'||ROUND(realized_pnl::numeric,2)||'|'||exit_reason||'|'||TO_CHAR(opened_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI')||'|'||TO_CHAR(closed_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI') FROM paper_positions WHERE status='closed' ORDER BY realized_pnl DESC LIMIT 12;"
$PSQL -c "SELECT id||'|'||symbol||'|'||direction||'|'||ROUND(entry_price::numeric,8)||'|'||ROUND(COALESCE(realized_pnl,0)::numeric,2)||'|'||status||'|'||TO_CHAR(opened_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI') FROM paper_positions WHERE status IN ('open','pending') ORDER BY opened_at DESC;"
