#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot

echo "cooldown_keys=$(docker compose exec -T redis redis-cli KEYS 'signal:cooldown:*' | wc -l)"

for k in SKRUSDT BLURUSDT SIGNUSDT KASUSDT PHAUSDT TREEUSDT CVCUSDT EDGEUSDT; do
  key="signal:cooldown:${k}:1h"
  typ=$(docker compose exec -T redis redis-cli TYPE "$key")
  ttl=$(docker compose exec -T redis redis-cli TTL "$key")
  echo "=== $k type=$typ ttl=${ttl}s ==="
  if [[ "$typ" == "hash" ]]; then
    docker compose exec -T redis redis-cli HGETALL "$key"
  fi
done

docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT d.status, d.suppression_reason, d.created_at,
       s.direction, round(s.score::numeric,2) AS score
FROM signal_deliveries d
JOIN signals s ON s.id = d.signal_id
JOIN assets a ON a.id = s.asset_id
WHERE a.symbol = 'SKRUSDT'
  AND d.created_at > NOW() - INTERVAL '24 hours'
ORDER BY d.created_at DESC
LIMIT 20;
SQL
