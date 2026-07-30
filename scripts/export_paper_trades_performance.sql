-- Pipe-delimited export for paper-trades-performance canvas (all positions + KPIs).
-- Dollar-KPIs sind bei schwankender Positionsgroesse nicht vergleichbar; die
-- R-Kennzahlen (RKPI/CONC/TRADE.r) sind die eigentliche Bewertungsgrundlage.
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
-- Risikonormierte KPIs: n | totalR | expectancyR | feesR | winrate% | profit factor (R) | avg win R | avg loss R
SELECT 'RKPI|' || n || '|' || ROUND(total_r::numeric, 3) || '|' || ROUND(exp_r::numeric, 4)
  || '|' || ROUND(fees_r::numeric, 3) || '|' || ROUND((wr * 100)::numeric, 1)
  || '|' || ROUND(pf_r::numeric, 2) || '|' || ROUND(avg_win_r::numeric, 3)
  || '|' || ROUND(avg_loss_r::numeric, 3)
FROM (
  SELECT
    COUNT(*) AS n,
    COALESCE(SUM(r), 0) AS total_r,
    CASE WHEN COUNT(*) > 0 THEN COALESCE(SUM(r), 0) / COUNT(*) ELSE 0 END AS exp_r,
    COALESCE(SUM(fee_r), 0) AS fees_r,
    CASE WHEN COUNT(*) > 0
      THEN COUNT(*) FILTER (WHERE r > 0)::float / COUNT(*)
      ELSE 0
    END AS wr,
    CASE WHEN ABS(COALESCE(SUM(r) FILTER (WHERE r <= 0), 0)) > 0
      THEN COALESCE(SUM(r) FILTER (WHERE r > 0), 0)
        / ABS(COALESCE(SUM(r) FILTER (WHERE r <= 0), 0))
      ELSE 999
    END AS pf_r,
    COALESCE(AVG(r) FILTER (WHERE r > 0), 0) AS avg_win_r,
    COALESCE(AVG(r) FILTER (WHERE r <= 0), 0) AS avg_loss_r
  FROM (
    SELECT realized_pnl / risk_amount AS r, fees / risk_amount AS fee_r
    FROM paper_positions
    WHERE status = 'closed' AND risk_amount > 0
  ) t
) s;
-- Entries und maximale Parallelbelegung des Depots.
SELECT 'CONC|' || entries || '|' || COALESCE(max_open, 0)
FROM (
  SELECT
    (SELECT COUNT(*) FROM paper_fills WHERE reason = 'entry') AS entries,
    (
      SELECT MAX(running) FROM (
        SELECT SUM(delta) OVER (ORDER BY ts, delta ROWS UNBOUNDED PRECEDING) AS running
        FROM (
          SELECT opened_at AS ts, 1 AS delta
          FROM paper_positions WHERE status IN ('open', 'closed')
          UNION ALL
          SELECT closed_at AS ts, -1 AS delta
          FROM paper_positions WHERE closed_at IS NOT NULL
        ) ev
      ) r
    ) AS max_open
) c;
SELECT 'EXIT|' || COALESCE(exit_reason, 'unknown') || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric, 2), 0)
  || '|' || COALESCE(ROUND(SUM(realized_pnl / NULLIF(risk_amount, 0))::numeric, 3), 0)
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
  || COALESCE(TO_CHAR(closed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI'), '') || '|'
  || COALESCE(ROUND(risk_amount::numeric, 2), 0) || '|'
  || COALESCE(ROUND((realized_pnl / NULLIF(risk_amount, 0))::numeric, 3)::text, '') || '|'
  || COALESCE(ROUND((fees / NULLIF(risk_amount, 0))::numeric, 4)::text, '')
FROM paper_positions
ORDER BY opened_at ASC;
