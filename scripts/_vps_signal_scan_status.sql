\echo === COUNTS ===
SELECT
  COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '6 hours') AS h6,
  COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') AS h24,
  COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days') AS d7,
  COUNT(*) FILTER (WHERE is_dispatched AND created_at > NOW() - INTERVAL '7 days') AS d7_disp,
  COUNT(*) FILTER (WHERE is_dispatched AND created_at > NOW() - INTERVAL '24 hours') AS h24_disp
FROM signals;

\echo === LAST 25 SIGNALS ===
SELECT s.created_at, a.symbol, s.direction, ROUND(s.score::numeric, 1) AS score,
       s.confidence, s.market_phase, s.is_dispatched,
       ROUND(s.risk_reward_ratio::numeric, 2) AS rr
FROM signals s
JOIN assets a ON a.id = s.asset_id
ORDER BY s.created_at DESC
LIMIT 25;

\echo === SCORE BUCKETS 7d ===
SELECT
  CASE
    WHEN score >= 75 THEN '75+'
    WHEN score >= 70 THEN '70-74'
    WHEN score >= 65 THEN '65-69'
    WHEN score >= 60 THEN '60-64'
    ELSE '<60'
  END AS bucket,
  COUNT(*) AS n,
  ROUND(AVG(score)::numeric, 1) AS avg_score,
  SUM(CASE WHEN is_dispatched THEN 1 ELSE 0 END) AS dispatched
FROM signals
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY 1
ORDER BY 1;

\echo === NEAR MISSES 48h score 65-74.99 ===
SELECT s.created_at, a.symbol, s.direction, ROUND(s.score::numeric, 1) AS score,
       s.confidence, s.market_phase, s.is_dispatched,
       ROUND(s.risk_reward_ratio::numeric, 2) AS rr
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '48 hours'
  AND s.score >= 65 AND s.score < 75
ORDER BY s.score DESC, s.created_at DESC
LIMIT 30;

\echo === TOP SCORES 48h ===
SELECT s.created_at, a.symbol, s.direction, ROUND(s.score::numeric, 1) AS score,
       s.confidence, s.market_phase, s.is_dispatched
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '48 hours'
ORDER BY s.score DESC
LIMIT 20;

\echo === MIX 7d ===
SELECT direction, confidence, COUNT(*) AS n,
       SUM(CASE WHEN is_dispatched THEN 1 ELSE 0 END) AS dispatched,
       ROUND(MAX(score)::numeric, 1) AS max_score
FROM signals
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY 1, 2
ORDER BY n DESC;

\echo === SCAN EVENTS ===
SELECT created_at, event_type, LEFT(message, 160) AS message
FROM events
WHERE event_type ILIKE '%scan%'
ORDER BY created_at DESC
LIMIT 12;
