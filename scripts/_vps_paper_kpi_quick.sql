\pset tuples_only on
\pset format unaligned
SELECT status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric, 2), 0)
FROM paper_positions GROUP BY status ORDER BY status;
SELECT 'ACCT|' || COALESCE(ROUND(realized_pnl::numeric, 2), 0) || '|' || COALESCE(ROUND(cash_balance::numeric, 2), 0)
FROM paper_accounts WHERE name = 'default';
SELECT 'WR|' ||
  COUNT(*) FILTER (WHERE realized_pnl > 0) || '|' ||
  COUNT(*) FILTER (WHERE realized_pnl <= 0) || '|' ||
  ROUND(100.0 * COUNT(*) FILTER (WHERE realized_pnl > 0) / NULLIF(COUNT(*), 0), 1)
FROM paper_positions WHERE status = 'closed';
