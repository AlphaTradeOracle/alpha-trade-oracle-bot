#!/usr/bin/env bash
# Audit DB size vs Top-400 universe — report only.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"

docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
\pset border 1
\echo === UNIVERSE ===
SELECT COUNT(*) FILTER (WHERE in_universe) AS in_universe,
       COUNT(*) FILTER (WHERE NOT in_universe) AS out_universe,
       COUNT(*) AS assets_total
FROM assets;

\echo === CANDLES BY UNIVERSE ===
SELECT CASE WHEN a.in_universe THEN 'in' ELSE 'out' END AS uni,
       COUNT(*) AS candles,
       pg_size_pretty(SUM(pg_column_size(c.*))::bigint) AS approx_row_bytes
FROM candles c
JOIN assets a ON a.id = c.asset_id
GROUP BY 1
ORDER BY 1;

\echo === SIGNALS BY UNIVERSE (30d) ===
SELECT CASE WHEN a.in_universe THEN 'in' ELSE 'out' END AS uni,
       COUNT(*) AS signals
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '30 days'
GROUP BY 1 ORDER BY 1;

\echo === OUT-OF-UNIVERSE ASSETS WITH DATA ===
SELECT a.symbol, a.market_cap_rank,
       (SELECT COUNT(*) FROM candles c WHERE c.asset_id=a.id) AS candles,
       (SELECT COUNT(*) FROM signals s WHERE s.asset_id=a.id) AS signals
FROM assets a
WHERE NOT a.in_universe
  AND (
    EXISTS (SELECT 1 FROM candles c WHERE c.asset_id=a.id)
    OR EXISTS (SELECT 1 FROM signals s WHERE s.asset_id=a.id)
  )
ORDER BY candles DESC NULLS LAST
LIMIT 40;

\echo === TABLE SIZES ===
SELECT relname AS table,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
       n_live_tup AS live_est
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
WHERE n.nspname='public' AND c.relkind='r'
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT 20;

\echo === OLD CANDLES (> retention window hint) ===
SELECT COUNT(*) FILTER (WHERE open_time < NOW() - INTERVAL '365 days') AS older_than_365d,
       COUNT(*) FILTER (WHERE open_time < NOW() - INTERVAL '180 days') AS older_than_180d,
       COUNT(*) AS candles_total,
       MIN(open_time) AS oldest,
       MAX(open_time) AS newest
FROM candles;

\echo === PAPER / JOBS clutter ===
SELECT 'paper_positions' AS t, COUNT(*) FROM paper_positions
UNION ALL SELECT 'scan_jobs', COUNT(*) FROM scan_jobs
UNION ALL SELECT 'signal_suppressions', COUNT(*) FROM signal_suppressions
UNION ALL SELECT 'equity_snapshots', COUNT(*) FROM paper_equity_snapshots;
SQL
