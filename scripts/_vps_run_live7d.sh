#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

git pull --ff-only origin main || true
docker compose up -d --build worker

# Live stack: whatever is in .env (ADX30, no STRONG, TP 1.5/2.5/4, score 75, …)
# Variant "baseline" = current settings with no overrides.
nohup docker compose exec -T -e PYTHONUNBUFFERED=1 worker \
  python scripts/optimize_strategy_top300.py \
    --top 300 --days 7 --workers 2 --timeframe 1h \
    --only baseline \
    --out /tmp/backtest_live_top300_7d.json \
  > exports/backtest_live_top300_7d.log 2>&1 &

echo "PID $!"
sleep 8
tail -n 25 exports/backtest_live_top300_7d.log
grep -E '^(SIGNAL_|TP_|MIN_RISK)' .env | head -20
