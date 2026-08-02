\pset tuples_only on
\pset format unaligned
SELECT json_build_object(
  'account', (
    SELECT row_to_json(a) FROM (
      SELECT name, initial_balance::float AS initial_balance,
             cash_balance::float AS cash_balance,
             realized_pnl::float AS realized_pnl,
             margin_per_trade::float AS margin_per_trade,
             leverage::float AS leverage
      FROM paper_accounts WHERE name = 'default'
    ) a
  ),
  'positions', COALESCE((
    SELECT json_agg(row_to_json(p) ORDER BY p.opened_at DESC) FROM (
      SELECT id, symbol, direction, status, timeframe,
             entry_price::float AS entry_price,
             stop_loss::float AS stop_loss,
             current_stop::float AS current_stop,
             take_profit_1::float AS take_profit_1,
             take_profit_2::float AS take_profit_2,
             take_profit_3::float AS take_profit_3,
             initial_quantity::float AS initial_quantity,
             remaining_quantity::float AS remaining_quantity,
             margin_used::float AS margin_used,
             notional::float AS notional,
             leverage::float AS leverage,
             tp1_filled, tp2_filled, tp3_filled,
             realized_pnl::float AS realized_pnl,
             fees::float AS fees,
             risk_amount::float AS risk_amount,
             CASE WHEN risk_amount > 0
               THEN (realized_pnl / risk_amount)::float
               ELSE NULL END AS r_multiple,
             signal_score::float AS signal_score,
             exit_reason, notes,
             opened_at, closed_at, expires_at
      FROM paper_positions
    ) p
  ), '[]'::json),
  'fills', COALESCE((
    SELECT json_agg(row_to_json(f) ORDER BY f.filled_at, f.id) FROM (
      SELECT id, position_id, reason, price::float AS price,
             quantity::float AS quantity, fee::float AS fee,
             pnl::float AS pnl, filled_at
      FROM paper_fills
    ) f
  ), '[]'::json)
);
