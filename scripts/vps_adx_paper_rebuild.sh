#!/usr/bin/env bash
# DEPRECATED: ADX=35 rejected after live rebuild (15 closed, PF 0.41). Use vps_mtf_paper_rebuild.sh.
# Kept for reference only — do not run in production.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
export PGPASSWORD

run_sql() {
  docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
    psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A -f - "$@"
}

echo "==> Ensure SIGNAL_MIN_ADX=35 in .env"
sed -i 's/^SIGNAL_MIN_ADX=.*/SIGNAL_MIN_ADX=35/' .env
grep -q '^SIGNAL_MIN_ADX=' .env || echo 'SIGNAL_MIN_ADX=35' >> .env
grep '^SIGNAL_MIN_ADX=' .env

echo "==> Pull + rebuild containers"
git pull --ff-only
docker compose build app worker
docker compose up -d app worker

echo "==> Verify ADX setting in worker"
docker compose exec -T worker python -c \
  'from app.core.config import get_settings; print("signal_min_adx", get_settings().signal_min_adx)'

echo "==> BASELINE KPIs (before paper rebuild)"
run_sql <<'SQL'
\pset tuples_only on
\pset format unaligned
SELECT 'BASELINE|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions GROUP BY status ORDER BY status;
SELECT 'WR|' || wins || '|' || losses || '|' || ROUND(wr*100,1) || '|' || pf || '|' || closed_n
FROM (
  SELECT
    COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
    COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses,
    COUNT(*) AS closed_n,
    CASE WHEN COUNT(*)>0 THEN COUNT(*) FILTER (WHERE realized_pnl > 0)::float/COUNT(*) ELSE 0 END AS wr,
    CASE WHEN ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0),0)) > 0
      THEN ROUND((COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl > 0),0) / ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0),0)))::numeric,2)
      ELSE 999 END AS pf
  FROM paper_positions WHERE status='closed'
) s;
SELECT 'ACCT|' || COALESCE(ROUND(realized_pnl::numeric,2),0) || '|' || COALESCE(ROUND(cash_balance::numeric,2),0)
FROM paper_accounts WHERE name='default';
SQL

echo "==> Paper rebuild (all signals since 2026-07-28, ADX gate via settings)"
docker compose run --rm worker python -m app.cli paper rebuild \
  --since 2026-07-28T00:00:00+00:00 --all-signals

echo "==> AFTER KPIs"
run_sql <<'SQL'
\pset tuples_only on
\pset format unaligned
SELECT 'AFTER|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions GROUP BY status ORDER BY status;
SELECT 'WR|' || wins || '|' || losses || '|' || ROUND(wr*100,1) || '|' || pf || '|' || closed_n
FROM (
  SELECT
    COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
    COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses,
    COUNT(*) AS closed_n,
    CASE WHEN COUNT(*)>0 THEN COUNT(*) FILTER (WHERE realized_pnl > 0)::float/COUNT(*) ELSE 0 END AS wr,
    CASE WHEN ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0),0)) > 0
      THEN ROUND((COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl > 0),0) / ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0),0)))::numeric,2)
      ELSE 999 END AS pf
  FROM paper_positions WHERE status='closed'
) s;
SELECT 'ACCT|' || COALESCE(ROUND(realized_pnl::numeric,2),0) || '|' || COALESCE(ROUND(cash_balance::numeric,2),0)
FROM paper_accounts WHERE name='default';
SQL

echo "Done."
