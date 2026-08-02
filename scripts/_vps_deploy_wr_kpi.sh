#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
git fetch origin main cursor/trading-dashboard-efe9
git reset --hard origin/main
docker compose build app
docker compose up -d --no-deps app
bash scripts/_vps_deploy_dashboard_only.sh
sleep 2
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import sys,json
p=json.load(sys.stdin)["portfolio"]
print("openR", p.get("openR"), "winRatePct", p.get("winRatePct"), "closed", p.get("closedTrades"))
'
