#!/usr/bin/env bash
# Top-400 / 30d TP-ladder + adapted-combo sweep on the worker.
# 1 worker: ProcessPool forks duplicate candle RAM; 2 workers OOM'd (~8GB host).
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

ONLY="baseline,tp_123,tp_tight,tp_246,tp_wide,tp_358,tp_4812,tp_246_equal,tp_wide_no_be,tp_wide_exp48,combo_adx30_wide,combo_adx30_wide_rr,combo_adx25_246_exp48,combo_score78_wide,combo_adx30_4812"
OUT=exports/tp_ladder_top400_30d.json
LOG=exports/tp_ladder_top400_30d.log
mkdir -p exports scripts

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) tp ladder top400 start =====" | tee "$LOG"
echo "ONLY=$ONLY" | tee -a "$LOG"
echo "workers=1 (OOM-safe)" | tee -a "$LOG"

docker cp scripts/optimize_strategy_top300.py alpha-trade-oracle-worker:/app/scripts/optimize_strategy_top300.py

# Drop page cache pressure a bit before the heavy load
sync || true

docker compose exec -T \
  -e PYTHONUNBUFFERED=1 \
  worker python scripts/optimize_strategy_top300.py \
  --top 400 \
  --days 30 \
  --timeframe 1h \
  --workers 1 \
  --only "$ONLY" \
  --out "$OUT" \
  2>&1 | tee -a "$LOG"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) tp ladder top400 done =====" | tee -a "$LOG"
ls -lah "$OUT" | tee -a "$LOG"
