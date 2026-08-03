#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
docker compose exec -T worker python -m app.cli paper reset
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import sys, json
d = json.load(sys.stdin)
p = d.get("portfolio") or {}
print(
    "equity", p.get("equity"),
    "cash", p.get("cash"),
    "realized", p.get("realizedPnl"),
    "open", p.get("openPositions"),
    "pending", p.get("pendingOrders"),
    "closed", p.get("closedTrades"),
)
'
# clear signal cooldowns so fresh scans can open again
docker compose exec -T redis redis-cli KEYS 'signal:cooldown:*' | head -5 || true
docker compose exec -T redis redis-cli --scan --pattern 'signal:cooldown:*' | while read -r k; do
  [ -n "$k" ] && docker compose exec -T redis redis-cli DEL "$k" >/dev/null
done
echo cooldowns_cleared
