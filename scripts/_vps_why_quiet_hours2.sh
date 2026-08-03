#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot

docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
\echo === LAST 3H: SHORT SCORE HISTOGRAM (all SHORT/STRONG_SHORT) ===
SELECT width_bucket(score::float, 10, 50, 8) AS bucket,
       count(*),
       round(min(score)::numeric,2) AS min_s,
       round(max(score)::numeric,2) AS max_s
FROM signals
WHERE created_at > NOW() - INTERVAL '3 hours'
  AND direction IN ('SHORT','STRONG_SHORT')
GROUP BY 1 ORDER BY 1;

\echo === LAST 3H: NO_TRADE top reasons ===
SELECT left(COALESCE(no_trade_reason,'(null)'), 80) AS reason, count(*)
FROM signals
WHERE created_at > NOW() - INTERVAL '3 hours' AND direction='NO_TRADE'
GROUP BY 1 ORDER BY 2 DESC LIMIT 15;

\echo === LAST 3H: closest shorts to gate (any score) ===
SELECT a.symbol, s.direction, round(s.score::numeric,2) AS score, s.created_at,
       s.is_dispatched, left(COALESCE(s.no_trade_reason,''), 50) AS note
FROM signals s JOIN assets a ON a.id=s.asset_id
WHERE s.created_at > NOW() - INTERVAL '3 hours'
  AND s.direction IN ('SHORT','STRONG_SHORT','NO_TRADE')
  AND s.score <= 40
ORDER BY s.score ASC
LIMIT 25;

\echo === SKR details ===
SELECT a.symbol, s.direction, round(s.score::numeric,2), s.created_at, s.is_dispatched,
       s.data_quality, s.risk_reward_ratio,
       left(COALESCE(s.no_trade_reason,''), 80),
       left(s.reasons::text, 200) AS reasons
FROM signals s JOIN assets a ON a.id=s.asset_id
WHERE a.symbol='SKRUSDT' AND s.created_at > NOW() - INTERVAL '6 hours'
ORDER BY s.created_at DESC LIMIT 10;

\echo === universe now + last scan size hint ===
SELECT count(*) FILTER (WHERE in_universe) AS uni FROM assets;
SELECT max(last_scanned_at) AS last_scan_any FROM assets WHERE in_universe;
SELECT count(*) FILTER (WHERE last_scanned_at > NOW() - INTERVAL '45 minutes') AS scanned_last_45m
FROM assets WHERE in_universe;
SQL

echo "=== latest scan_completed ==="
docker compose logs worker --since 1h 2>&1 | grep scan_completed | tail -5
