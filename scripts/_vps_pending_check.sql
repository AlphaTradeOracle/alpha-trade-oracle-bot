SELECT p.id, p.symbol, p.direction, p.status, p.signal_score, p.opened_at, p.closed_at, p.exit_reason
FROM paper_positions p
JOIN paper_accounts a ON a.id = p.account_id
WHERE a.name = 'default' AND p.symbol = 'SKRUSDT'
ORDER BY p.opened_at DESC
LIMIT 10;

SELECT
  COUNT(*) FILTER (WHERE status = 'pending') AS pending,
  COUNT(*) FILTER (WHERE status = 'open') AS open,
  COUNT(*) FILTER (WHERE status = 'closed') AS closed,
  COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled
FROM paper_positions p
JOIN paper_accounts a ON a.id = p.account_id
WHERE a.name = 'default';

SELECT s.created_at, ast.symbol, s.direction, s.score, s.is_dispatched
FROM signals s
JOIN assets ast ON ast.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '12 hours'
  AND (
    (s.direction = 'SHORT' AND s.score BETWEEN 18 AND 30)
    OR (s.direction = 'LONG' AND s.score >= 75)
  )
ORDER BY s.created_at DESC
LIMIT 20;

SELECT exit_reason, COUNT(*) AS n
FROM paper_positions p
JOIN paper_accounts a ON a.id = p.account_id
WHERE a.name = 'default' AND p.status = 'cancelled'
GROUP BY 1
ORDER BY n DESC
LIMIT 15;
