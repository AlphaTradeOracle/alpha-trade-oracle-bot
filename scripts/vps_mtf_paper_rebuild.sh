#!/usr/bin/env bash
# Deploy MTF weights, rescore signals, rebuild paper ledger, print KPIs.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
export PGPASSWORD

run_sql() {
  docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
    psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A -f - "$@"
}

echo "==> Pull + rebuild containers"
git pull --ff-only
docker compose build app worker
docker compose up -d app worker

echo "==> Verify weights"
docker exec alpha-trade-oracle-app python -c \
  'from app.strategies.weights import DEFAULT_WEIGHTS as w; print("trend", w.trend, "mtf", w.multi_timeframe)'

echo "==> BASELINE KPIs (before rescore/rebuild)"
run_sql <<'SQL'
\pset tuples_only on
\pset format unaligned
SELECT 'BASELINE|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
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
SQL

echo "==> Activate strategy weights"
docker compose run --rm worker python scripts/activate_strategy_weights.py

echo "==> Rescore all signals with new weights"
docker compose run --rm worker python scripts/rescore_signals_with_weights.py

echo "==> Paper rebuild (all signals since 2026-07-28)"
docker compose run --rm worker python -m app.cli paper rebuild \
  --since 2026-07-28T00:00:00+00:00 --all-signals

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
FROM paper_positions WHERE status='closed' ORDER BY realized_pnl DESC LIMIT 5;
SELECT 'BOT|' || symbol || '|' || ROUND(realized_pnl::numeric,2) || '|' || exit_reason
FROM paper_positions WHERE status='closed' ORDER BY realized_pnl ASC LIMIT 5;
SELECT 'OPEN|' || id || '|' || symbol || '|' || direction || '|' || status || '|' || ROUND(realized_pnl::numeric,2)
FROM paper_positions WHERE status IN ('open','pending') ORDER BY opened_at DESC;
SQL

echo "Done."
