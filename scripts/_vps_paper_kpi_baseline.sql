\pset tuples_only on
\pset format unaligned
SELECT 'BASELINE|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions GROUP BY status ORDER BY status;
SELECT 'KPI|' || COUNT(*) FILTER (WHERE status='closed') || '|' ||
  COUNT(*) FILTER (WHERE status='open') || '|' ||
  COUNT(*) FILTER (WHERE status='pending') || '|' ||
  COUNT(*) FILTER (WHERE status='cancelled') || '|' ||
  COALESCE(ROUND(SUM(realized_pnl) FILTER (WHERE status='closed')::numeric,2),0) || '|' ||
  COALESCE(ROUND((SELECT realized_pnl FROM paper_accounts WHERE name='default')::numeric,2),0)
FROM paper_positions;
SELECT 'WR|' || wins || '|' || losses || '|' || ROUND(wr*100,1) || '|' || pf
FROM (
  SELECT
    COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
    COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses,
    CASE WHEN COUNT(*)>0 THEN COUNT(*) FILTER (WHERE realized_pnl > 0)::float/COUNT(*) ELSE 0 END AS wr,
    CASE WHEN ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0),0)) > 0
      THEN ROUND((COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl > 0),0) / ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0),0)))::numeric,2)
      ELSE 999 END AS pf
  FROM paper_positions WHERE status='closed'
) s;
SELECT 'RKPI|' || COUNT(*) || '|' || ROUND(COALESCE(SUM(r),0)::numeric,3) || '|' ||
  ROUND((CASE WHEN COUNT(*)>0 THEN COALESCE(SUM(r),0)/COUNT(*) ELSE 0 END)::numeric,4) || '|' ||
  ROUND(COALESCE(SUM(fee_r),0)::numeric,3)
FROM (
  SELECT realized_pnl / risk_amount AS r, fees / risk_amount AS fee_r
  FROM paper_positions WHERE status='closed' AND risk_amount > 0
) t;
SELECT 'TOP|' || symbol || '|' || ROUND(realized_pnl::numeric,2) || '|' || exit_reason
FROM paper_positions WHERE status='closed' ORDER BY realized_pnl DESC LIMIT 5;
SELECT 'BOT|' || symbol || '|' || ROUND(realized_pnl::numeric,2) || '|' || exit_reason
FROM paper_positions WHERE status='closed' ORDER BY realized_pnl ASC LIMIT 5;
