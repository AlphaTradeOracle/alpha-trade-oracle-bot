#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) WHY NO PENDING ====="

docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
\echo === CURRENT BOOK ===
SELECT status, count(*) FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
GROUP BY 1 ORDER BY 1;

\echo === PENDING ANY ===
SELECT id, symbol, direction, status, opened_at, expires_at, entry_price, stop_loss
FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
  AND status IN ('pending','open')
ORDER BY opened_at DESC;

\echo === PAPER-GATE SIGNALS LAST 12H (should arm retest) ===
SELECT a.symbol, s.direction, round(s.score::numeric,2) AS score,
       s.created_at, s.is_dispatched,
       s.stop_loss IS NOT NULL AS has_sl,
       s.take_profit_1 IS NOT NULL AS has_tp1,
       round(s.risk_reward_ratio::numeric,2) AS rr,
       round(s.data_quality::numeric,1) AS dq
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '12 hours'
  AND (
    (s.direction IN ('SHORT','STRONG_SHORT') AND s.score > 18 AND s.score <= 30)
    OR (s.direction IN ('LONG','STRONG_LONG') AND s.score >= 75)
  )
ORDER BY s.created_at DESC
LIMIT 40;

\echo === SKR paper history ===
SELECT id, symbol, status, direction, opened_at, closed_at, exit_reason,
       round(realized_pnl::numeric,2) AS pnl
FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
  AND symbol='SKRUSDT'
ORDER BY opened_at DESC
LIMIT 15;
SQL

echo "=== CONFIG ==="
grep -E '^PAPER_RETEST|^ENABLE_PAPER|^TELEGRAM_SIGNAL|^SIGNAL_SHORT|^SIGNAL_MIN_SCORE|^PAPER_MAX_OPEN' .env

echo "=== WORKER LOG: paper skips / SKR / pending (6h) ==="
docker compose logs worker --since 6h 2>&1 | grep -iE 'SKR|paper_skip|paper_position_pending|paper_position_retest|skipped_|open_from|suppressed|cooldown|busy|circuit|blackout|portfolio' | tail -60

echo "=== last scan_completed ==="
docker compose logs worker --since 2h 2>&1 | grep scan_completed | tail -5
