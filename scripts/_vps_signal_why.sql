\echo === DISPATCHED 7d ===
SELECT s.created_at, a.symbol, s.direction, ROUND(s.score::numeric,1) AS score,
       s.confidence, s.market_phase, ROUND(s.risk_reward_ratio::numeric,2) AS rr
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.is_dispatched
  AND s.created_at > NOW() - INTERVAL '7 days'
ORDER BY s.created_at DESC;

\echo === HIGH SCORE NOT DISPATCHED 48h (score>=75) ===
SELECT s.created_at, a.symbol, s.direction, ROUND(s.score::numeric,1) AS score,
       s.confidence, s.market_phase,
       LEFT(COALESCE(s.counter_arguments::text,''), 180) AS counters,
       LEFT(COALESCE(s.reasons::text,''), 120) AS reasons
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '48 hours'
  AND s.score >= 75
  AND NOT s.is_dispatched
ORDER BY s.score DESC
LIMIT 25;

\echo === STRONG_* last 48h ===
SELECT s.created_at, a.symbol, s.direction, ROUND(s.score::numeric,1) AS score,
       s.confidence, s.market_phase, s.is_dispatched
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '48 hours'
  AND s.direction LIKE 'STRONG%'
ORDER BY s.created_at DESC
LIMIT 30;

\echo === LONG score>=74 last 24h (knapp) ===
SELECT s.created_at, a.symbol, s.direction, ROUND(s.score::numeric,1) AS score,
       s.confidence, s.market_phase, s.is_dispatched
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '24 hours'
  AND s.score >= 74
ORDER BY s.score DESC
LIMIT 40;
