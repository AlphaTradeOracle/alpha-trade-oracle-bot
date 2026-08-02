SELECT status, COUNT(*) AS n FROM paper_positions GROUP BY status ORDER BY n DESC;

SELECT id, symbol, status, exit_reason, realized_pnl::float AS pnl
FROM paper_positions
WHERE status IN ('cancelled', 'closed') OR exit_reason = 'retest_skipped'
ORDER BY COALESCE(closed_at, opened_at) DESC
LIMIT 25;
