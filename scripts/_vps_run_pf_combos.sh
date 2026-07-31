#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
sed -i 's/\r$//' /tmp/optimize_strategy_top300.py
docker cp /tmp/optimize_strategy_top300.py alpha-trade-oracle-worker:/app/scripts/optimize_strategy_top300.py
mkdir -p exports
chmod 777 exports || true

ONLY='ref_adx20,adx_30,combo_adx30_tp,combo_adx30_tp_rr,combo_adx30_tp_mom,combo_pf_stack'

# Force base ADX unrelated: variants set min_adx explicitly where needed.
# tp_tight/rr/momentum inherit live settings (ADX30) — pin them via only-list
# and ensure ref_adx20 is the old baseline.

nohup docker compose exec -T -e PYTHONUNBUFFERED=1 worker \
  python scripts/optimize_strategy_top300.py \
    --top 50 --days 30 --workers 2 \
    --only "$ONLY" \
    --out /tmp/optimize_pf_combos.json \
  > exports/optimize_pf_combos.log 2>&1 &
echo "PID $!"
sleep 8
tail -n 20 exports/optimize_pf_combos.log
