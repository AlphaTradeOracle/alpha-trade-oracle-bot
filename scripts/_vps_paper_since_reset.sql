-- Paper positions since last ledger reset.
\pset tuples_only on
\pset format unaligned
\pset fieldsep '|'

SELECT 'META';
SELECT 'cash=' || cash_balance::text
    || '|realized=' || realized_pnl::text
    || '|initial=' || initial_balance::text
FROM paper_accounts
LIMIT 1;

SELECT 'CLOSED';
SELECT id::text
    || '|' || symbol
    || '|' || direction
    || '|' || ROUND(signal_score::numeric, 2)::text
    || '|' || ROUND(COALESCE(realized_pnl, 0)::numeric, 2)::text
    || '|' || COALESCE(
          CASE WHEN risk_amount > 0
            THEN ROUND((realized_pnl / risk_amount)::numeric, 2)::text
            ELSE NULL END,
          ''
       )
    || '|' || COALESCE(exit_reason, '')
    || '|' || opened_at::text
    || '|' || COALESCE(closed_at::text, '')
    || '|' || ROUND(COALESCE(entry_price, 0)::numeric, 8)::text
    || '|' || ROUND(COALESCE(stop_loss, 0)::numeric, 8)::text
    || '|' || ROUND(COALESCE(risk_amount, 0)::numeric, 2)::text
FROM paper_positions
WHERE status = 'closed'
ORDER BY opened_at;

SELECT 'OPEN';
SELECT id::text
    || '|' || symbol
    || '|' || direction
    || '|' || status
    || '|' || ROUND(signal_score::numeric, 2)::text
    || '|' || ROUND(COALESCE(entry_price, 0)::numeric, 8)::text
    || '|' || ROUND(COALESCE(realized_pnl, 0)::numeric, 2)::text
    || '|' || opened_at::text
FROM paper_positions
WHERE status IN ('open', 'pending')
ORDER BY status, opened_at;
