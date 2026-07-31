#!/usr/bin/env bash
# Top-500 baseline compare (ranks 1-300 vs 301-500) on VPS worker.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

OUT=/tmp/universe_300_vs_500_30d.json
LOG=exports/universe_300_vs_500_30d.log
mkdir -p exports

echo "==> Ensure compare script in image (rebuild if missing)"
if ! docker compose exec -T worker test -f /app/scripts/compare_universe_topn.py; then
  docker compose build worker
  docker compose up -d worker
  sleep 3
fi

echo "==> Start baseline compare top500 / 30d / 1h"
# Copy host script in case image is stale after git pull without rebuild
docker compose cp scripts/compare_universe_topn.py worker:/app/scripts/compare_universe_topn.py
docker compose cp scripts/optimize_strategy_top300.py worker:/app/scripts/optimize_strategy_top300.py

nohup docker compose exec -T worker python /app/scripts/compare_universe_topn.py \
  --top 500 --days 30 --timeframe 1h --workers 2 \
  --out "$OUT" \
  >"$LOG" 2>&1 &

echo "PID $!  log=$LOG  out=$OUT"
echo "Tail with: tail -f $LOG"
