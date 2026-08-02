\pset format aligned
SELECT status, COUNT(*) n,
       ROUND(SUM(realized_pnl)::numeric,2) sum_pnl,
       ROUND(SUM(margin_used)::numeric,2) sum_m,
       ROUND(AVG(notional)::numeric,2) avg_n
FROM paper_positions GROUP BY status ORDER BY status;

SELECT ROUND(realized_pnl::numeric,2) acct_realized,
       ROUND(cash_balance::numeric,2) cash,
       ROUND(margin_per_trade::numeric,2) margin_setting
FROM paper_accounts WHERE name='default';

SELECT symbol, status,
       ROUND(realized_pnl::numeric,2) pnl,
       ROUND(margin_used::numeric,2) m,
       ROUND(notional::numeric,2) n,
       ROUND(risk_amount::numeric,2) r,
       tp1_filled, tp2_filled,
       ROUND(stop_loss::numeric,6) sl,
       ROUND(current_stop::numeric,6) cs,
       ROUND(initial_quantity::numeric,4) iq,
       ROUND(remaining_quantity::numeric,4) rq
FROM paper_positions
WHERE status IN ('open','closed')
ORDER BY status, opened_at;
