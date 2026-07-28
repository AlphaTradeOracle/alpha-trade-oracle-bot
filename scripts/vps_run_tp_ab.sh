#!/bin/bash
set -euo pipefail
SP=/opt/venv/lib/python3.12/site-packages/app
docker cp /tmp/engine.py "alpha-trade-oracle-worker:${SP}/backtesting/engine.py"
docker cp /tmp/risk.py "alpha-trade-oracle-worker:${SP}/signals/risk.py"
docker cp /tmp/run_tp_ab_backtests.py alpha-trade-oracle-worker:/app/scripts/run_tp_ab_backtests.py
docker compose -f /opt/alpha-trade-oracle-bot/docker-compose.yml exec -T worker python /tmp/_verify_tp_field.py
cd /opt/alpha-trade-oracle-bot
docker compose exec -T worker python scripts/run_tp_ab_backtests.py --top 20 --days 28 --timeframe 1h > /tmp/tp_ab_out.json 2> /tmp/tp_ab_err.log
echo DONE
tail -60 /tmp/tp_ab_err.log
wc -c /tmp/tp_ab_out.json
