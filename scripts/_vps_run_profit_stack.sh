#!/usr/bin/env bash
# Paper-autopsy profit stack: Top-100 / 14d / 6 variants / sequential.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

ONLY="A_base,B_tp1,C_tp_scratch,D_short_gate,E_retest_deep,F_stack"
OUT=exports/profit_stack_top100_14d.json
LOG=exports/profit_stack_top100_14d.log
mkdir -p exports

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) profit stack start =====" | tee "$LOG"
echo "ONLY=$ONLY workers=1 top=100 days=14" | tee -a "$LOG"

docker cp scripts/optimize_strategy_top300.py alpha-trade-oracle-worker:/app/scripts/optimize_strategy_top300.py
docker cp app/backtesting/engine.py alpha-trade-oracle-worker:/app/app/backtesting/engine.py

docker compose exec -T -e PYTHONUNBUFFERED=1 worker python scripts/optimize_strategy_top300.py \
  --top 100 \
  --days 14 \
  --timeframe 1h \
  --workers 1 \
  --fee 0.05 \
  --slippage 0.0 \
  --only "$ONLY" \
  --out "$OUT" \
  2>&1 | tee -a "$LOG"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) profit stack done =====" | tee -a "$LOG"
ls -lah "$OUT" | tee -a "$LOG"
