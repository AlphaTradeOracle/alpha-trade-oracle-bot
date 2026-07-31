\echo === ACCOUNT ===
SELECT id, cash_balance, equity, realized_pnl, fees_paid, starting_balance, updated_at
FROM paper_accounts;

\echo === ALL POSITIONS ===
SELECT id, symbol, direction, status,
       ROUND(margin_used::numeric, 2) AS margin,
       ROUND(notional::numeric, 2) AS notional,
       leverage,
       ROUND(entry_price::numeric, 6) AS entry,
       ROUND(realized_pnl::numeric, 2) AS rpnl,
       ROUND(fees::numeric, 4) AS fees,
       opened_at, closed_at, exit_reason
FROM paper_positions
ORDER BY id;

\echo === MARGIN SUM BY STATUS ===
SELECT status,
       COUNT(*) AS n,
       ROUND(SUM(margin_used)::numeric, 2) AS margin_sum,
       ROUND(SUM(notional)::numeric, 2) AS notional_sum,
       ROUND(SUM(fees)::numeric, 4) AS fees_sum,
       ROUND(SUM(realized_pnl)::numeric, 2) AS rpnl_sum
FROM paper_positions
GROUP BY status
ORDER BY status;
