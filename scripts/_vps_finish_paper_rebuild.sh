#!/usr/bin/env bash
# Finish soft-blend + paper rebuild (weights already rescored).
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

SINCE="${SINCE:-2026-07-31T00:00:00+00:00}"
export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
export PGPASSWORD

run_sql() {
  docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
    psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A -f -
}

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) finish paper rebuild ====="
git fetch origin main
git reset --hard origin/main
docker compose build worker
docker compose up -d --no-deps worker

echo "==> Soft-blend rescore (1h-cached) since ${SINCE}"
docker compose run --rm --no-deps worker python scripts/rescore_signals_regime_soft_blend.py \
  --since "${SINCE}"

echo "==> Paper rebuild"
docker compose run --rm --no-deps worker python -m app.cli paper rebuild \
  --since "${SINCE}" --all-signals

echo "==> AFTER KPIs"
run_sql <<'SQL'
\pset tuples_only on
\pset format unaligned
SELECT 'AFTER|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions GROUP BY status ORDER BY status;
SELECT 'WR|' || wins || '|' || losses || '|' || ROUND(wr*100,1) || '|' || pf
FROM (
  SELECT
    COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
    COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses,
    CASE WHEN COUNT(*)>0 THEN COUNT(*) FILTER (WHERE realized_pnl > 0)::float/COUNT(*) ELSE 0 END AS wr,
    CASE WHEN ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0),0)) > 0
      THEN ROUND((COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl > 0),0) / ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0),0)))::numeric,2)
      ELSE 999 END AS pf
  FROM paper_positions WHERE status='closed'
) s;
SELECT 'ACCT|' || COALESCE(ROUND(realized_pnl::numeric,2),0) || '|' || COALESCE(ROUND(cash_balance::numeric,2),0)
FROM paper_accounts WHERE name='default';
SELECT 'TOP|' || symbol || '|' || ROUND(realized_pnl::numeric,2) || '|' || exit_reason
FROM paper_positions WHERE status='closed' ORDER BY realized_pnl DESC LIMIT 8;
SELECT 'BOT|' || symbol || '|' || ROUND(realized_pnl::numeric,2) || '|' || exit_reason
FROM paper_positions WHERE status='closed' ORDER BY realized_pnl ASC LIMIT 8;
SELECT 'OPEN|' || id || '|' || symbol || '|' || direction || '|' || status || '|' || ROUND(COALESCE(realized_pnl,0)::numeric,2) || '|' || ROUND(signal_score::numeric,1)
FROM paper_positions WHERE status IN ('open','pending') ORDER BY opened_at DESC;
SQL
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) finish paper rebuild done ====="
