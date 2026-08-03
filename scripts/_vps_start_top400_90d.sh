#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

docker ps -q --filter name=worker-run | xargs -r docker rm -f
pkill -9 -f 'run_top400_paper_parity_90d.py' 2>/dev/null || true
sleep 2

mkdir -p exports
: > /tmp/top400_90d_bt.log

nohup docker compose run --rm --no-deps \
  -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
  -v /opt/alpha-trade-oracle-bot/exports:/app/exports \
  worker python /app/scripts/run_top400_paper_parity_90d.py \
    --top 400 --days 90 --workers 2 \
    --out /app/exports/top400_paper_parity_90d.json \
  >> /tmp/top400_90d_bt.log 2>&1 &

echo "STARTED_PID=$!"
sleep 10
tail -n 40 /tmp/top400_90d_bt.log
