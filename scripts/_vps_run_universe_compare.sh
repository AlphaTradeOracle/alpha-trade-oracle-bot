#!/usr/bin/env bash
# Top-500 baseline compare vs Top-300 — same conditions as live7d top300 test.
# Conditions: 7d · 1h · baseline · workers=2 · $5k/symbol · 0.05% fee+slip
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

OUT=/tmp/universe_300_vs_500_7d.json
LOG=exports/universe_300_vs_500_7d.log
mkdir -p exports

# Kill any prior stuck compare
pkill -f 'compare_universe_topn.py' 2>/dev/null || true

echo "==> Sync compare scripts into worker"
docker compose cp scripts/compare_universe_topn.py worker:/app/scripts/compare_universe_topn.py
docker compose cp scripts/optimize_strategy_top300.py worker:/app/scripts/optimize_strategy_top300.py

echo "==> Start baseline compare top500 / 7d / 1h (match top300 live7d)"
nohup docker compose exec -T -e PYTHONUNBUFFERED=1 worker \
  python /app/scripts/compare_universe_topn.py \
    --top 500 --days 7 --timeframe 1h --workers 2 \
    --out "$OUT" \
  >"$LOG" 2>&1 &

echo "PID $!  log=$LOG  out=$OUT"
sleep 5
tail -n 30 "$LOG" || true
