\echo === scheduled_jobs ===
SELECT job_key, job_type, interval_seconds,
       last_run_at AT TIME ZONE 'UTC' AS last_run_utc,
       next_run_at AT TIME ZONE 'UTC' AS next_run_utc,
       last_status, LEFT(COALESCE(last_error,''),80) AS err, run_count, is_enabled
FROM scheduled_jobs
ORDER BY COALESCE(last_run_at, next_run_at) DESC NULLS LAST
LIMIT 20;

\echo === in_band actionable 7d (short 18-25 / long >=75) ===
SELECT s.created_at AT TIME ZONE 'UTC' AS utc,
       a.symbol, s.direction, ROUND(s.score::numeric,1) AS score,
       s.is_dispatched,
       LEFT(COALESCE(s.invalidation_note,''), 80) AS note
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '7 days'
  AND (
    (s.direction IN ('SHORT','STRONG_SHORT') AND s.score > 18 AND s.score <= 25)
    OR (s.direction IN ('LONG','STRONG_LONG') AND s.score >= 75)
  )
ORDER BY s.created_at DESC
LIMIT 40;

\echo === near-miss shorts 48h (just outside band) ===
SELECT s.created_at AT TIME ZONE 'UTC' AS utc,
       a.symbol, s.direction, ROUND(s.score::numeric,1) AS score,
       s.is_dispatched,
       LEFT(COALESCE(s.reasons::text,''), 120) AS reasons
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '48 hours'
  AND s.direction IN ('SHORT','STRONG_SHORT','NO_TRADE')
  AND (
    (s.score > 25 AND s.score <= 28)
    OR (s.score >= 16 AND s.score <= 18)
  )
ORDER BY s.created_at DESC
LIMIT 30;

\echo === near-miss longs 48h (just outside band) ===
SELECT s.created_at AT TIME ZONE 'UTC' AS utc,
       a.symbol, s.direction, ROUND(s.score::numeric,1) AS score,
       s.is_dispatched
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '48 hours'
  AND s.direction IN ('LONG','STRONG_LONG','NO_TRADE')
  AND s.score BETWEEN 72 AND 78
ORDER BY s.created_at DESC
LIMIT 30;

\echo === dispatched last 7d ===
SELECT s.created_at AT TIME ZONE 'UTC' AS utc,
       a.symbol, s.direction, ROUND(s.score::numeric,1) AS score
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '7 days'
  AND s.is_dispatched = true
ORDER BY s.created_at DESC
LIMIT 30;

\echo === paper last 7d ===
SELECT opened_at AT TIME ZONE 'UTC' AS utc, symbol, direction,
       ROUND(signal_score::numeric,1) AS score, status
FROM paper_positions
WHERE COALESCE(opened_at, created_at) > NOW() - INTERVAL '7 days'
ORDER BY COALESCE(opened_at, created_at) DESC
LIMIT 30;

\echo === scan throughput 12h ===
SELECT date_trunc('hour', s.created_at) AT TIME ZONE 'UTC' AS hour_utc,
       COUNT(*) AS signals,
       COUNT(*) FILTER (WHERE s.direction IN ('SHORT','STRONG_SHORT') AND s.score > 18 AND s.score <= 25) AS short_band,
       COUNT(*) FILTER (WHERE s.direction IN ('LONG','STRONG_LONG') AND s.score >= 75) AS long_band,
       COUNT(*) FILTER (WHERE s.is_dispatched) AS dispatched
FROM signals s
WHERE s.created_at > NOW() - INTERVAL '12 hours'
GROUP BY 1
ORDER BY 1 DESC;
