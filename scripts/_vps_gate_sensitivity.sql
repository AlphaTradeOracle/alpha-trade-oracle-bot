\echo === ACTIONABLE candidates 7d (direction not NEUTRAL) ===
SELECT
  COUNT(*) FILTER (WHERE direction IN ('LONG','STRONG_LONG','SHORT','STRONG_SHORT')) AS actionable,
  COUNT(*) FILTER (WHERE direction LIKE 'STRONG%') AS strong,
  COUNT(*) FILTER (WHERE direction IN ('LONG','SHORT')) AS plain,
  COUNT(*) FILTER (WHERE is_dispatched) AS dispatched
FROM signals
WHERE created_at > NOW() - INTERVAL '7 days';

\echo === Counterfactual: what would pass gate combos (7d, longs+shorts) ===
-- Note: NO_TRADE already failed engine gates (ADX/RSI/range/RR). Those stay out.
WITH base AS (
  SELECT direction, score, confidence, market_phase, is_dispatched,
         risk_reward_ratio AS rr
  FROM signals
  WHERE created_at > NOW() - INTERVAL '7 days'
    AND direction IN ('LONG','STRONG_LONG','SHORT','STRONG_SHORT')
)
SELECT
  'A live: STRONG + long>=75 / short<=25' AS gate,
  COUNT(*) FILTER (
    WHERE direction LIKE 'STRONG%'
      AND ((direction LIKE '%LONG' AND score >= 75)
        OR (direction LIKE '%SHORT' AND score <= 25 AND score >= 18))
  ) AS would_pass
FROM base
UNION ALL
SELECT
  'B drop STRONG: long>=75 / short<=25',
  COUNT(*) FILTER (
    WHERE (direction LIKE '%LONG' AND score >= 75)
       OR (direction LIKE '%SHORT' AND score <= 25 AND score >= 18)
  )
FROM base
UNION ALL
SELECT
  'C STRONG + long>=70',
  COUNT(*) FILTER (
    WHERE direction LIKE 'STRONG%'
      AND ((direction LIKE '%LONG' AND score >= 70)
        OR (direction LIKE '%SHORT' AND score <= 30 AND score >= 18))
  )
FROM base
UNION ALL
SELECT
  'D no STRONG + long>=70',
  COUNT(*) FILTER (
    WHERE (direction LIKE '%LONG' AND score >= 70)
       OR (direction LIKE '%SHORT' AND score <= 30 AND score >= 18)
  )
FROM base
UNION ALL
SELECT
  'E STRONG only (any score)',
  COUNT(*) FILTER (WHERE direction LIKE 'STRONG%')
FROM base;

\echo === STRONG_LONG 7d by score band ===
SELECT
  CASE
    WHEN score >= 85 THEN '85+'
    WHEN score >= 80 THEN '80-84'
    WHEN score >= 75 THEN '75-79'
    WHEN score >= 70 THEN '70-74'
    ELSE '<70'
  END AS band,
  COUNT(*) AS n,
  SUM(CASE WHEN is_dispatched THEN 1 ELSE 0 END) AS dispatched
FROM signals
WHERE created_at > NOW() - INTERVAL '7 days'
  AND direction = 'STRONG_LONG'
GROUP BY 1
ORDER BY 1;

\echo === LONG (non-strong) score>=70 7d ===
SELECT
  CASE
    WHEN score >= 75 THEN '75+'
    WHEN score >= 70 THEN '70-74'
    ELSE 'other'
  END AS band,
  COUNT(*) AS n,
  ROUND(AVG(score)::numeric,1) AS avg_score
FROM signals
WHERE created_at > NOW() - INTERVAL '7 days'
  AND direction = 'LONG'
  AND score >= 70
GROUP BY 1
ORDER BY 1;

\echo === NO_TRADE with score>=75 last 7d (engine-killed) ===
SELECT COUNT(*) AS n,
       ROUND(AVG(score)::numeric,1) AS avg_score,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY score)::numeric,1) AS median
FROM signals
WHERE created_at > NOW() - INTERVAL '7 days'
  AND direction = 'NO_TRADE'
  AND score >= 75;

\echo === dispatched per day 7d ===
SELECT DATE(created_at) AS day, COUNT(*) AS dispatched
FROM signals
WHERE is_dispatched AND created_at > NOW() - INTERVAL '10 days'
GROUP BY 1
ORDER BY 1;
