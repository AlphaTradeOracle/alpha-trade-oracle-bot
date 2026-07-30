-- Pipe-delimited export for paper-trades-performance canvas (all positions + KPIs).
-- Run on VPS: bash scripts/vps_export_paper_trades_performance.sh
\pset tuples_only on
\pset format unaligned
SELECT 'META|' || TO_CHAR(NOW() AT TIME ZONE 'Europe/Berlin', 'YYYY-MM-DD HH24:MI') || ' Europe/Berlin';
SELECT 'ACCOUNT|' || initial_balance || '|' || cash_balance || '|' || realized_pnl || '|' || margin_per_trade || '|' || leverage
FROM paper_accounts WHERE name = 'default';
SELECT 'STATUS|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric, 2), 0)
FROM paper_positions GROUP BY status ORDER BY status;
SELECT 'WR|' || wins || '|' || losses || '|' || ROUND((wr * 100)::numeric, 1) || '|' || pf
FROM (
  SELECT
    COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
    COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses,
    CASE WHEN COUNT(*) > 0
      THEN COUNT(*) FILTER (WHERE realized_pnl > 0)::float / COUNT(*)
      ELSE 0
    END AS wr,
    CASE WHEN ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0), 0)) > 0
      THEN ROUND(
        (COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl > 0), 0)
          / ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0), 0)))::numeric,
        2
      )
      ELSE 999
    END AS pf
  FROM paper_positions WHERE status = 'closed'
) s;
SELECT 'EXIT|' || COALESCE(exit_reason, 'unknown') || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric, 2), 0)
FROM paper_positions WHERE status = 'closed' GROUP BY exit_reason ORDER BY COUNT(*) DESC;
SELECT 'TRADE|'
  || id || '|'
  || symbol || '|'
  || direction || '|'
  || status || '|'
  || ROUND(entry_price::numeric, 8) || '|'
  || ROUND(stop_loss::numeric, 8) || '|'
  || ROUND(current_stop::numeric, 8) || '|'
  || ROUND(take_profit_1::numeric, 8) || '|'
  || ROUND(take_profit_2::numeric, 8) || '|'
  || ROUND(take_profit_3::numeric, 8) || '|'
  || CASE WHEN tp1_filled THEN '1' ELSE '0' END || '|'
  || CASE WHEN tp2_filled THEN '1' ELSE '0' END || '|'
  || CASE WHEN tp3_filled THEN '1' ELSE '0' END || '|'
  || COALESCE(ROUND(realized_pnl::numeric, 2), 0) || '|'
  || COALESCE(exit_reason, '') || '|'
  || TO_CHAR(opened_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') || '|'
  || COALESCE(TO_CHAR(closed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI'), '')
FROM paper_positions
ORDER BY opened_at ASC;
