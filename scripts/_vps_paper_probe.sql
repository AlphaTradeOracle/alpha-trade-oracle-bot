SELECT name,
       ROUND(initial_balance::numeric, 2) AS ib,
       ROUND(cash_balance::numeric, 2) AS cash,
       ROUND(realized_pnl::numeric, 2) AS realized,
       ROUND(margin_per_trade::numeric, 2) AS mpt,
       leverage
FROM paper_accounts;

SELECT status,
       COUNT(*) AS n,
       ROUND(AVG(margin_used)::numeric, 2) AS avg_m,
       ROUND(AVG(notional)::numeric, 2) AS avg_n,
       ROUND(AVG(risk_amount)::numeric, 2) AS avg_risk,
       ROUND(SUM(realized_pnl)::numeric, 2) AS sum_pnl
FROM paper_positions
GROUP BY status
ORDER BY 1;

SELECT ROUND(margin_used::numeric, 2) AS m,
       COUNT(*) AS n
FROM paper_positions
WHERE status IN ('open', 'pending')
GROUP BY 1
ORDER BY 1;
