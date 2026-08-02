\pset border 1
\echo === UNIVERSE ===
SELECT COUNT(*) FILTER (WHERE in_universe) AS in_universe,
       COUNT(*) FILTER (WHERE NOT in_universe) AS out_universe,
       COUNT(*) AS assets_total
FROM assets;

\echo === CANDLES BY UNIVERSE ===
SELECT CASE WHEN a.in_universe THEN 'in' ELSE 'out' END AS uni,
       COUNT(*) AS candles
FROM market_candles c
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

\echo === OUT-OF-UNIVERSE WITH DATA ===
SELECT a.symbol, a.market_cap_rank,
       (SELECT COUNT(*) FROM market_candles c WHERE c.asset_id=a.id) AS candles,
       (SELECT COUNT(*) FROM signals s WHERE s.asset_id=a.id) AS signals
FROM assets a
WHERE NOT a.in_universe
  AND (
    EXISTS (SELECT 1 FROM market_candles c WHERE c.asset_id=a.id)
    OR EXISTS (SELECT 1 FROM signals s WHERE s.asset_id=a.id)
  )
ORDER BY candles DESC NULLS LAST
LIMIT 30;

\echo === TABLE SIZES ===
SELECT relname AS tbl,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname='public' AND c.relkind='r'
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT 15;

\echo === CANDLE AGE ===
SELECT COUNT(*) FILTER (WHERE open_time < NOW() - INTERVAL '365 days') AS older_365d,
       COUNT(*) FILTER (WHERE open_time < NOW() - INTERVAL '180 days') AS older_180d,
       COUNT(*) AS candles_total,
       MIN(open_time) AS oldest,
       MAX(open_time) AS newest
FROM market_candles;

\echo === COUNTS ===
SELECT 'paper_positions' AS t, COUNT(*)::text FROM paper_positions
UNION ALL SELECT 'signals', COUNT(*)::text FROM signals
UNION ALL SELECT 'market_candles', COUNT(*)::text FROM market_candles
UNION ALL SELECT 'indicator_snapshots', COUNT(*)::text FROM indicator_snapshots
UNION ALL SELECT 'scheduled_jobs', COUNT(*)::text FROM scheduled_jobs
UNION ALL SELECT 'application_events', COUNT(*)::text FROM application_events;
