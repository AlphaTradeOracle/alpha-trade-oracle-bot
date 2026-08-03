#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
set -a; . ./.env; set +a

SHORT_MAX="${SIGNAL_SHORT_MAX_SCORE:-30}"
SHORT_MIN="${SIGNAL_SHORT_MIN_SCORE:-18}"
LONG_MIN="${SIGNAL_MIN_SCORE:-75}"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) QUIET HOURS ====="
echo "gates short ${SHORT_MIN}-${SHORT_MAX} long>=${LONG_MIN}"

docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<SQL
\echo === SCAN JOB ===
SELECT job_key, last_status, last_run_at, last_success_at, next_run_at,
       left(COALESCE(last_error,''), 100) AS err
FROM scheduled_jobs WHERE job_key LIKE '%scan%';

\echo === SIGNALS LAST 6H BY HOUR (all / actionable dir / paper-gate) ===
SELECT date_trunc('hour', created_at) AS h,
       count(*) AS all_rows,
       count(*) FILTER (WHERE direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')) AS actionable_dir,
       count(*) FILTER (
         WHERE direction IN ('SHORT','STRONG_SHORT')
           AND score > ${SHORT_MIN}::float AND score <= ${SHORT_MAX}::float
       ) AS short_paper_gate,
       count(*) FILTER (
         WHERE direction IN ('LONG','STRONG_LONG') AND score >= ${LONG_MIN}::float
       ) AS long_paper_gate,
       count(*) FILTER (WHERE is_dispatched) AS dispatched
FROM signals
WHERE created_at > NOW() - INTERVAL '6 hours'
GROUP BY 1 ORDER BY 1;

\echo === LAST 3H: DIRECTION BREAKDOWN ===
SELECT direction, count(*),
       round(min(score)::numeric,2) AS min_s,
       round(avg(score)::numeric,2) AS avg_s,
       round(max(score)::numeric,2) AS max_s
FROM signals
WHERE created_at > NOW() - INTERVAL '3 hours'
GROUP BY 1 ORDER BY 2 DESC;

\echo === LAST 3H: SHORT NEAR MISS (score just above short_max) ===
SELECT round(score::numeric,2) AS score, count(*) AS n,
       string_agg(DISTINCT left(symbol, 20), ', ' ORDER BY left(symbol,20)) AS symbols
FROM (
  SELECT s.score, a.symbol
  FROM signals s
  JOIN assets a ON a.id = s.asset_id
  WHERE s.created_at > NOW() - INTERVAL '3 hours'
    AND s.direction IN ('SHORT','STRONG_SHORT')
    AND s.score > ${SHORT_MAX}::float AND s.score <= ${SHORT_MAX}::float + 5
) x
GROUP BY 1 ORDER BY 1
LIMIT 25;

\echo === LAST 3H: BEST SHORTS (lowest scores) ===
SELECT a.symbol, s.direction, round(s.score::numeric,2) AS score,
       s.created_at, s.is_dispatched,
       left(COALESCE(s.skip_reason,''), 60) AS skip_reason
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '3 hours'
  AND s.direction IN ('SHORT','STRONG_SHORT')
ORDER BY s.score ASC
LIMIT 20;

\echo === LAST 3H: BEST LONGS (highest scores) ===
SELECT a.symbol, s.direction, round(s.score::numeric,2) AS score, s.created_at
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '3 hours'
  AND s.direction IN ('LONG','STRONG_LONG')
ORDER BY s.score DESC
LIMIT 15;

\echo === LAST 3H: WOULD PASS PAPER SCORE GATE ===
SELECT a.symbol, s.direction, round(s.score::numeric,2) AS score, s.created_at, s.is_dispatched
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '3 hours'
  AND (
    (s.direction IN ('SHORT','STRONG_SHORT') AND s.score > ${SHORT_MIN}::float AND s.score <= ${SHORT_MAX}::float)
    OR (s.direction IN ('LONG','STRONG_LONG') AND s.score >= ${LONG_MIN}::float)
  )
ORDER BY s.created_at DESC
LIMIT 30;

\echo === PAPER POSITIONS LAST 3H ===
SELECT status, count(*) FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
  AND COALESCE(opened_at, created_at) > NOW() - INTERVAL '3 hours'
GROUP BY 1;
SQL

echo "=== WORKER SCAN LOG TAIL ==="
docker compose logs worker --since 3h 2>&1 | grep -iE 'market_scan|scan_complete|scan_finished|universe_scan|signal_created|paper_skip|no_actionable' | tail -40 || true
