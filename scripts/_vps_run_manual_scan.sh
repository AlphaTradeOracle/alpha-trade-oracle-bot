#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
# Kill stale manual scans inside worker if any
docker compose exec -T worker bash -lc 'pkill -f "app.cli scan" || true' || true
nohup docker compose exec -T -e PYTHONUNBUFFERED=1 worker \
  python -m app.cli scan --universe --dispatch \
  > /tmp/manual_scan.log 2>&1 &
echo "started pid=$!"
sleep 3
head -n 6 /tmp/manual_scan.log
