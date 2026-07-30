-- Export paper trades + signal fields for filter simulation (pipe-delimited).
-- Run on VPS:
--   docker exec -e PGPASSWORD=... alpha-trade-oracle-postgres \
--     psql -U alpha_trade_oracle -d alpha_trade_oracle -f scripts/export_paper_filters_data.sql
\pset tuples_only on
\pset format unaligned

SELECT 'META|' || TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || '|UTC';

SELECT 'TRADE|'
  || p.id || '|'
  || p.symbol || '|'
  || p.direction || '|'
  || p.status || '|'
  || COALESCE(p.timeframe, '1h') || '|'
  || ROUND(p.realized_pnl::numeric, 4) || '|'
  || COALESCE(p.exit_reason, '') || '|'
  || TO_CHAR(p.opened_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || '|'
  || COALESCE(TO_CHAR(p.closed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'), '') || '|'
  || COALESCE(p.signal_id::text, '') || '|'
  || COALESCE(ROUND(p.signal_score::numeric, 2)::text, '') || '|'
  || COALESCE(REPLACE(COALESCE(p.notes, ''), '|', '/'), '') || '|'
  || COALESCE(
       (
         SELECT ROUND(i.adx_14::numeric, 2)::text
         FROM indicator_snapshots i
         WHERE i.asset_id = p.asset_id
           AND i.timeframe = COALESCE(p.timeframe, '1h')
           AND i.candle_open_time <= p.opened_at
           AND i.adx_14 IS NOT NULL
         ORDER BY i.candle_open_time DESC
         LIMIT 1
       ),
       ''
     )
FROM paper_positions p
ORDER BY p.opened_at;

SELECT 'SIGNAL|'
  || s.id || '|'
  || s.direction || '|'
  || ROUND(s.score::numeric, 2) || '|'
  || ROUND(s.data_quality::numeric, 2) || '|'
  || COALESCE(ROUND(s.risk_reward_ratio::numeric, 4)::text, '') || '|'
  || s.market_phase || '|'
  || s.primary_timeframe || '|'
  || s.confidence || '|'
  || TO_CHAR(s.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
FROM signals s
WHERE s.id IN (SELECT DISTINCT signal_id FROM paper_positions WHERE signal_id IS NOT NULL)
ORDER BY s.id;
