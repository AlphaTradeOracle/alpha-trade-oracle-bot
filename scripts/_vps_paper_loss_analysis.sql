-- Paper trade loss autopsy since Top-400 reset
\echo === ACCOUNT ===
SELECT cash_balance, realized_pnl, initial_balance,
       cash_balance + COALESCE((
         SELECT SUM(margin_used) FROM paper_positions WHERE status='open'
       ),0) AS equity_approx
FROM paper_accounts LIMIT 1;

\echo === STATUS COUNTS ===
SELECT status, COUNT(*),
       ROUND(SUM(COALESCE(realized_pnl,0))::numeric, 2) AS sum_pnl
FROM paper_positions
GROUP BY 1 ORDER BY 1;

\echo === CLOSED BY EXIT REASON ===
SELECT COALESCE(exit_reason,'(null)') AS exit_reason,
       COUNT(*) AS n,
       ROUND(SUM(realized_pnl)::numeric, 2) AS sum_pnl,
       ROUND(AVG(realized_pnl)::numeric, 2) AS avg_pnl,
       ROUND(100.0 * AVG(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), 1) AS win_pct,
       ROUND(SUM(fees)::numeric, 2) AS fees
FROM paper_positions
WHERE status = 'closed'
GROUP BY 1
ORDER BY sum_pnl;

\echo === CLOSED DETAIL ===
SELECT id, symbol, direction,
       ROUND(signal_score::numeric, 1) AS score,
       ROUND(entry_price::numeric, 6) AS entry,
       ROUND(stop_loss::numeric, 6) AS sl,
       ROUND(realized_pnl::numeric, 2) AS pnl,
       ROUND(fees::numeric, 2) AS fees,
       ROUND(risk_amount::numeric, 2) AS risk,
       CASE WHEN risk_amount > 0
         THEN ROUND((realized_pnl / risk_amount)::numeric, 2) ELSE NULL END AS r_mult,
       tp1_filled, tp2_filled, tp3_filled,
       exit_reason,
       opened_at, closed_at,
       ROUND(EXTRACT(EPOCH FROM (closed_at - opened_at))/3600.0::numeric, 1) AS hold_h,
       left(coalesce(notes,''), 60) AS notes
FROM paper_positions
WHERE status = 'closed'
ORDER BY closed_at;

\echo === OPEN / PENDING ===
SELECT id, symbol, direction, status,
       ROUND(signal_score::numeric, 1) AS score,
       ROUND(entry_price::numeric, 6) AS entry,
       ROUND(margin_used::numeric, 2) AS margin,
       ROUND(realized_pnl::numeric, 2) AS pnl_so_far,
       tp1_filled, opened_at, expires_at,
       left(coalesce(notes,''), 80) AS notes
FROM paper_positions
WHERE status IN ('open','pending')
ORDER BY status, opened_at;

\echo === CANCELLED / RETEST SKIP ===
SELECT id, symbol, direction, exit_reason,
       opened_at, closed_at,
       left(coalesce(notes,''), 100) AS notes
FROM paper_positions
WHERE status = 'cancelled'
ORDER BY closed_at DESC NULLS LAST;

\echo === FILLS SUMMARY PER CLOSED ===
SELECT p.symbol, p.exit_reason,
       COUNT(f.id) AS fills,
       ROUND(SUM(f.pnl)::numeric, 2) AS fill_pnl,
       ROUND(SUM(f.fee)::numeric, 2) AS fill_fees,
       string_agg(f.reason, ',' ORDER BY f.filled_at) AS reasons
FROM paper_positions p
JOIN paper_fills f ON f.position_id = p.id
WHERE p.status = 'closed'
GROUP BY p.id, p.symbol, p.exit_reason
ORDER BY fill_pnl;

\echo === DIRECTION SPLIT CLOSED ===
SELECT direction,
       COUNT(*) AS n,
       ROUND(SUM(realized_pnl)::numeric, 2) AS sum_pnl,
       ROUND(100.0 * AVG(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), 1) AS win_pct
FROM paper_positions
WHERE status = 'closed'
GROUP BY 1;
