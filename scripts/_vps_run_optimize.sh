#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
sed -i 's/\r$//' /tmp/optimize_strategy_top300.py
docker cp /tmp/optimize_strategy_top300.py alpha-trade-oracle-worker:/app/scripts/optimize_strategy_top300.py
mkdir -p exports
chmod 777 exports || true

# 2 vCPU host → max 2 workers. Focused variant set for timely canvas.
# Top-50 of Top-300 for timely ranking on 2 vCPU; covers all main axes.
ONLY='baseline,score_70,score_78,no_strong,adx_25,adx_30,rr_2_5,ist_entry,tp_tight,w_boost_trend,w_boost_momentum,combo_quality'

nohup docker compose exec -T -e PYTHONUNBUFFERED=1 worker \
  python scripts/optimize_strategy_top300.py \
    --top 50 --days 30 --workers 2 \
    --only "$ONLY" \
    --out /tmp/optimize_top300_30d.json \
  > exports/optimize_top300_30d.log 2>&1 &
echo "PID $!"
sleep 10
tail -n 20 exports/optimize_top300_30d.log
