#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot

docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
\echo === signal suppressions / delivery for SKR last 24h ===
SELECT column_name FROM information_schema.columns
WHERE table_name IN ('signals','signal_deliveries','signal_suppressions')
ORDER BY table_name, ordinal_position;
SQL

# discover schema then query
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
\echo === SKR signals with suppression fields ===
SELECT s.id, s.direction, round(s.score::numeric,2) AS score, s.created_at,
       s.is_dispatched, s.suppression_reason
FROM signals s
JOIN assets a ON a.id=s.asset_id
WHERE a.symbol='SKRUSDT' AND s.created_at > NOW() - INTERVAL '24 hours'
ORDER BY s.created_at DESC
LIMIT 20;

\echo === cancelled/pending SKR ever since reset ===
SELECT id, status, direction, opened_at, closed_at, exit_reason,
       left(COALESCE(notes,''), 80) AS notes
FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
  AND symbol='SKRUSDT'
ORDER BY id DESC
LIMIT 20;

\echo === any cancelled last 12h (sample) ===
SELECT symbol, status, opened_at, closed_at, exit_reason,
       left(COALESCE(notes,''), 100) AS notes
FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
  AND status='cancelled'
  AND COALESCE(closed_at, opened_at) > NOW() - INTERVAL '12 hours'
ORDER BY COALESCE(closed_at, opened_at) DESC
LIMIT 25;
SQL

echo "=== REDIS cooldowns (SKR etc) ==="
docker compose exec -T redis redis-cli KEYS 'signal:cooldown:*' | head -50
docker compose exec -T redis redis-cli KEYS 'signal:cooldown:SKR*' 
for k in $(docker compose exec -T redis redis-cli KEYS 'signal:cooldown:SKR*'); do
  echo -n "$k TTL="; docker compose exec -T redis redis-cli TTL "$k"
  docker compose exec -T redis redis-cli GET "$k" | head -c 200; echo
done

echo "=== grep worker archive for SKR paper/cooldown ==="
docker compose logs worker --since 24h 2>&1 | grep -i SKR | grep -iE 'pending|paper|cooldown|skip|suppressed|deferred|telegram' | tail -40
