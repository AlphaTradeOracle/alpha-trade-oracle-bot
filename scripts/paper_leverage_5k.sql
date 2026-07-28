-- Paper depot: +$3000 capital (initial 2k -> 5k) and open positions 5x -> 10x.
BEGIN;

UPDATE paper_accounts
SET
  initial_balance = 5000,
  cash_balance = cash_balance + 3000,
  leverage = 10,
  margin_per_trade = 100,
  updated_at = NOW()
WHERE name = 'default';

-- Open inventory: same margin, double notional/qty for 10x.
UPDATE paper_positions
SET
  leverage = 10,
  notional = notional * 2,
  initial_quantity = initial_quantity * 2,
  remaining_quantity = remaining_quantity * 2,
  updated_at = NOW()
WHERE status = 'open';

-- Closed history: label leverage only (realized qty/PnL unchanged).
UPDATE paper_positions
SET
  leverage = 10,
  updated_at = NOW()
WHERE status = 'closed';

COMMIT;

-- Verify
SELECT name, initial_balance, cash_balance, realized_pnl, leverage, margin_per_trade
FROM paper_accounts WHERE name = 'default';

SELECT id, symbol, status, leverage, margin_used, notional,
       initial_quantity, remaining_quantity, realized_pnl
FROM paper_positions
ORDER BY id;
