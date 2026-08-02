#!/usr/bin/env bash
# 7D Top-50 short_min floor sweep — nohup-safe, no git hard-reset.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

OUT=exports/short_min_floor_7d_top50.json
LOG=exports/short_min_floor_7d_top50.log
RUNLOG=exports/short_min_floor_7d_top50.run.log

mkdir -p exports
# Kill prior incomplete run if still attached to same log (best-effort)
pkill -f 'backtest_short_min_floor.py' 2>/dev/null || true
sleep 1

git fetch origin main
# Sync script only (avoid resetting live .env / worker mid-paper)
git checkout origin/main -- scripts/backtest_short_min_floor.py app/backtesting/engine.py app/services/backtest_service.py 2>/dev/null || true

# Ensure worker image has latest scripts via bind mount or copy into running context
# Prefer docker compose run with project checkout (scripts live on host).
nohup docker compose run --rm --no-deps worker \
  python scripts/backtest_short_min_floor.py \
  --top 50 --days 7 --no-mtf --prefer-db \
  --out "$OUT" \
  >"$RUNLOG" 2>"$LOG" &
echo "PID=$!"
echo "Started $(date -u +%Y-%m-%dT%H:%M:%SZ) — log=$LOG runlog=$RUNLOG"
echo "Poll: tail -f $LOG"
