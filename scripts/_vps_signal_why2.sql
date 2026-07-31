\echo === CAP/TAG/UNI recent invalidation ===
SELECT s.created_at, a.symbol, s.direction, ROUND(s.score::numeric,1) AS score,
       s.confidence, s.is_dispatched,
       LEFT(COALESCE(s.invalidation_note,''), 200) AS inval,
       LEFT(COALESCE(s.counter_arguments::text,''), 220) AS counters
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE a.symbol IN ('CAPUSDT','TAGUSDT','UNIUSDT','MMTUSDT','PRLUSDT')
  AND s.created_at > NOW() - INTERVAL '24 hours'
  AND s.score >= 75
ORDER BY a.symbol, s.created_at DESC
LIMIT 40;

\echo === open paper ===
SELECT id, symbol, direction, status, opened_at, signal_score
FROM paper_positions
WHERE status IN ('open','pending')
ORDER BY opened_at DESC
LIMIT 30;
