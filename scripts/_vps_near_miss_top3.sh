#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
\pset border 2

\echo === TOP 3 LONG near-miss / best (48h, score DESC) ===
SELECT a.symbol,
       s.direction,
       ROUND(s.score::numeric, 2) AS score,
       s.confidence,
       ROUND(s.risk_reward_ratio::numeric, 2) AS rr,
       s.is_dispatched AS disp,
       s.created_at AT TIME ZONE 'UTC' AS created_utc
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '48 hours'
  AND s.direction IN ('LONG', 'STRONG_LONG', 'NO_TRADE')
  AND s.score >= 65
ORDER BY s.score DESC, s.created_at DESC
LIMIT 3;

\echo === TOP 3 SHORT near-miss / best (48h, lowest score = strongest short) ===
SELECT a.symbol,
       s.direction,
       ROUND(s.score::numeric, 2) AS score,
       s.confidence,
       ROUND(s.risk_reward_ratio::numeric, 2) AS rr,
       s.is_dispatched AS disp,
       s.created_at AT TIME ZONE 'UTC' AS created_utc
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '48 hours'
  AND s.direction IN ('SHORT', 'STRONG_SHORT', 'NO_TRADE')
  AND s.score <= 35
ORDER BY s.score ASC, s.created_at DESC
LIMIT 3;

\echo === TOP 3 overall by distance to band (48h) ===
-- Long band >=75, short band 18-25. Show closest actionable / near-miss.
WITH ranked AS (
  SELECT a.symbol,
         s.direction,
         ROUND(s.score::numeric, 2) AS score,
         s.confidence,
         ROUND(s.risk_reward_ratio::numeric, 2) AS rr,
         s.is_dispatched AS disp,
         s.created_at AT TIME ZONE 'UTC' AS created_utc,
         CASE
           WHEN s.direction IN ('LONG','STRONG_LONG') THEN 75 - s.score
           WHEN s.direction IN ('SHORT','STRONG_SHORT') AND s.score BETWEEN 18 AND 25 THEN 0
           WHEN s.direction IN ('SHORT','STRONG_SHORT') AND s.score > 25 THEN s.score - 25
           WHEN s.direction IN ('SHORT','STRONG_SHORT') AND s.score < 18 THEN 18 - s.score
           WHEN s.direction = 'NO_TRADE' AND s.score >= 50 THEN 75 - s.score
           WHEN s.direction = 'NO_TRADE' AND s.score < 50 THEN s.score - 25
           ELSE 999
         END AS distance_to_band,
         CASE
           WHEN s.direction IN ('LONG','STRONG_LONG') AND s.score >= 75 THEN 'in_band'
           WHEN s.direction IN ('SHORT','STRONG_SHORT') AND s.score BETWEEN 18 AND 25 THEN 'in_band'
           ELSE 'near'
         END AS band_status
  FROM signals s
  JOIN assets a ON a.id = s.asset_id
  WHERE s.created_at >= NOW() - INTERVAL '48 hours'
    AND s.direction IN ('LONG','STRONG_LONG','SHORT','STRONG_SHORT','NO_TRADE')
    AND (
      (s.direction IN ('LONG','STRONG_LONG','NO_TRADE') AND s.score >= 68)
      OR (s.direction IN ('SHORT','STRONG_SHORT','NO_TRADE') AND s.score <= 32)
    )
)
SELECT symbol, direction, score, confidence, rr, disp, band_status,
       ROUND(distance_to_band::numeric, 2) AS dist,
       created_utc
FROM ranked
WHERE distance_to_band < 20
ORDER BY
  CASE WHEN band_status = 'in_band' THEN 0 ELSE 1 END,
  ABS(distance_to_band),
  created_utc DESC
LIMIT 15;

\echo === last dispatched (48h) ===
SELECT a.symbol, s.direction, ROUND(s.score::numeric,2) score, s.is_dispatched,
       s.created_at AT TIME ZONE 'UTC' AS created_utc
FROM signals s JOIN assets a ON a.id = s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '48 hours' AND s.is_dispatched
ORDER BY s.created_at DESC
LIMIT 10;
SQL
