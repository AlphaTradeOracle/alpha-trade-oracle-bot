#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
docker compose exec -T worker python -m app.cli paper reset
# clear cooldowns
docker compose exec -T redis sh -c '
  redis-cli --scan --pattern "signal:cooldown:*" | while read -r k; do
    [ -n "$k" ] && redis-cli DEL "$k" >/dev/null
  done
  echo cooldowns_cleared
'
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import sys, json
p = json.load(sys.stdin).get("portfolio") or {}
print(
    "equity", p.get("equity"),
    "cash", p.get("cash"),
    "realized", p.get("realizedPnl"),
    "open", p.get("openPositions"),
    "pending", p.get("pendingOrders"),
    "closed", p.get("closedTrades"),
)
'
