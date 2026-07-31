\echo === ACCOUNT ===
SELECT id, ROUND(cash_balance::numeric, 2) AS cash,
       ROUND(realized_pnl::numeric, 2) AS realized,
       ROUND(fees_paid::numeric, 4) AS fees_paid,
       updated_at
FROM paper_accounts;

\echo === OPEN + PENDING ===
SELECT id, symbol, direction, status,
       ROUND(entry_price::numeric, 8) AS entry,
       ROUND(stop_loss::numeric, 8) AS sl,
       ROUND(current_stop::numeric, 8) AS cur_sl,
       ROUND(take_profit_1::numeric, 8) AS tp1,
       ROUND(take_profit_2::numeric, 8) AS tp2,
       ROUND(take_profit_3::numeric, 8) AS tp3,
       ROUND(margin_used::numeric, 2) AS margin,
       ROUND(notional::numeric, 2) AS notional,
       leverage,
       ROUND(realized_pnl::numeric, 2) AS rpnl,
       ROUND(fees::numeric, 4) AS fees,
       tp1_filled, tp2_filled, tp3_filled,
       ROUND(signal_score::numeric, 1) AS score,
       opened_at
FROM paper_positions
WHERE status IN ('open', 'pending')
ORDER BY status, opened_at;

\echo === CLOSED ALL ===
SELECT id, symbol, direction, status,
       ROUND(entry_price::numeric, 8) AS entry,
       ROUND(realized_pnl::numeric, 2) AS rpnl,
       ROUND(fees::numeric, 4) AS fees,
       exit_reason,
       opened_at, closed_at
FROM paper_positions
WHERE status = 'closed'
ORDER BY closed_at DESC NULLS LAST
LIMIT 50;

\echo === FILLS RECENT ===
SELECT f.position_id, p.symbol, f.reason, ROUND(f.pnl::numeric, 4) AS pnl,
       ROUND(f.fee::numeric, 4) AS fee, f.filled_at
FROM paper_fills f
JOIN paper_positions p ON p.id = f.position_id
ORDER BY f.filled_at DESC
LIMIT 30;
