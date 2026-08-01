#!/usr/bin/env bash
# Pull main, rebuild API app service, ensure desk snapshot is reachable locally.
set -euo pipefail

REPO="/opt/alpha-trade-oracle-bot"
cd "$REPO"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) desk API enable start ====="

git fetch origin main
git reset --hard origin/main

docker compose build app
docker compose up -d --no-deps app

echo "Waiting for API health..."
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "API healthy"
    break
  fi
  sleep 2
  if [[ "$i" -eq 30 ]]; then
    echo "ERROR: API failed healthcheck" >&2
    docker compose logs --tail=80 app || true
    exit 1
  fi
done

echo "--- /api/v1/desk/snapshot ---"
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import json,sys
d=json.load(sys.stdin)
p=d.get("portfolio",{})
trades=d.get("trades",[])
closed=[t for t in trades if t.get("status")=="CLOSED"]
zero=[t for t in closed if t.get("exit") is None]
print("generatedAt", d.get("generatedAt"))
print("open", p.get("openPositions"), "pending", p.get("pendingOrders"), "closed", p.get("closedTrades"))
print("trades", len(trades), "closed_rows", len(closed), "zero_exit_closed", len(zero))
'

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) desk API enable done ====="
