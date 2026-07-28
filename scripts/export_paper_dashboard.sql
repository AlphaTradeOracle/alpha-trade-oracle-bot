\pset tuples_only on
\pset format unaligned
SELECT json_build_object(
  'account', (
    SELECT row_to_json(a) FROM (
      SELECT name, initial_balance, cash_balance, realized_pnl, margin_per_trade, leverage
      FROM paper_accounts WHERE name = 'default'
    ) a
  ),
  'positions', COALESCE((
    SELECT json_agg(row_to_json(p) ORDER BY p.opened_at DESC) FROM (
      SELECT id, symbol, direction, status, timeframe,
             entry_price, stop_loss, current_stop,
             take_profit_1, take_profit_2, take_profit_3,
             initial_quantity, remaining_quantity, margin_used, notional, leverage,
             tp1_filled, tp2_filled, tp3_filled,
             realized_pnl, fees, signal_score, exit_reason, opened_at, closed_at
      FROM paper_positions
    ) p
  ), '[]'::json),
  'fills', COALESCE((
    SELECT json_agg(row_to_json(f) ORDER BY f.filled_at) FROM (
      SELECT id, position_id, reason, price, quantity, fee, pnl, filled_at
      FROM paper_fills
    ) f
  ), '[]'::json)
);
