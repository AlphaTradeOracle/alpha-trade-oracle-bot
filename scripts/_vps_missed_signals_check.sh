#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) missed-signals / uptodate check ====="
echo "bot=$(git rev-parse --short HEAD 2>/dev/null || echo '?')"

echo
echo "=== jobs ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT job_key, is_enabled, last_status, run_count,
       last_run_at, last_success_at, next_run_at,
       ROUND(EXTRACT(EPOCH FROM (NOW() - COALESCE(last_success_at, last_run_at)))/60.0, 1) AS mins_since_success,
       LEFT(COALESCE(last_error,''), 100) AS last_error
FROM scheduled_jobs
ORDER BY job_key;
SQL

echo
echo "=== signals per 15m bucket (last 6h) ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT
  date_trunc('hour', created_at)
    + (EXTRACT(MINUTE FROM created_at)::int / 15) * INTERVAL '15 minutes' AS bucket_utc,
  COUNT(*) AS n,
  COUNT(*) FILTER (WHERE is_dispatched) AS dispatched,
  COUNT(*) FILTER (
    WHERE direction IN ('SHORT','STRONG_SHORT') AND score BETWEEN 18 AND 25
  ) AS short_band,
  COUNT(*) FILTER (
    WHERE direction IN ('LONG','STRONG_LONG') AND score >= 75
  ) AS long_band
FROM signals
WHERE created_at >= NOW() - INTERVAL '6 hours'
GROUP BY 1
ORDER BY 1;
SQL

echo
echo "=== gaps > 20 min between scan batches (last 24h) ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
WITH batches AS (
  SELECT DISTINCT date_trunc('minute', created_at) AS t
  FROM signals
  WHERE created_at >= NOW() - INTERVAL '24 hours'
),
ordered AS (
  SELECT t, LAG(t) OVER (ORDER BY t) AS prev_t
  FROM batches
)
SELECT prev_t, t AS next_t,
       ROUND(EXTRACT(EPOCH FROM (t - prev_t))/60.0, 1) AS gap_min
FROM ordered
WHERE prev_t IS NOT NULL
  AND t - prev_t > INTERVAL '20 minutes'
ORDER BY gap_min DESC
LIMIT 20;
SQL

echo
echo "=== near-miss / actionable last 6h ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT a.symbol, s.direction, ROUND(s.score::numeric,1) AS score,
       s.is_dispatched, s.created_at
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '6 hours'
  AND (
    (s.direction IN ('SHORT','STRONG_SHORT') AND s.score <= 28)
    OR (s.direction IN ('LONG','STRONG_LONG') AND s.score >= 70)
    OR s.is_dispatched
  )
ORDER BY s.created_at DESC
LIMIT 25;
SQL

echo
echo "=== universe ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) FILTER (WHERE in_universe) AS in_universe, COUNT(*) AS total,
          MAX(updated_at) AS assets_updated_at FROM assets;"

echo
echo "=== worker / digest env ==="
docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Service}}' | grep -E 'NAME|worker|app' || docker compose ps
grep -E '^PAPER_HOURLY_DIGEST_ENABLED=' .env || true

echo "===== done ====="
