#!/usr/bin/env bash
set -eu
pkill -f '_vps_paper_reset_now' 2>/dev/null || true
pkill -f 'paper reset' 2>/dev/null || true
docker compose -f /opt/alpha-trade-oracle-bot/docker-compose.yml exec -T worker sh -c 'pkill -f "paper reset" || true' 2>/dev/null || true
sleep 1
pgrep -af 'paper reset|_vps_paper_reset' || echo STOPPED
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import sys, json
d = json.load(sys.stdin)
p = d.get("portfolio") or {}
print(
    "equity", p.get("equity"),
    "realized", p.get("realizedPnl"),
    "open", p.get("openPositions"),
    "pending", p.get("pendingOrders"),
    "closed", p.get("closedTrades"),
)
'
