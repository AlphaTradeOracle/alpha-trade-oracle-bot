#!/bin/bash
set -eu
SP=/opt/venv/lib/python3.12/site-packages/app
docker cp /tmp/engine.py "alpha-trade-oracle-worker:${SP}/backtesting/engine.py"
docker cp /tmp/risk.py "alpha-trade-oracle-worker:${SP}/signals/risk.py"
docker cp /tmp/run_tp_ab_backtests.py alpha-trade-oracle-worker:/app/scripts/run_tp_ab_backtests.py
cd /opt/alpha-trade-oracle-bot
docker compose exec -T worker python scripts/run_tp_ab_backtests.py --symbols BNBUSDT,DOGEUSDT,ZECUSDT,LINKUSDT,CCUSDT,GRAMUSDT --days 28 --timeframe 1h --no-mtf > /tmp/tp_ab_fast_out.json 2> /tmp/tp_ab_fast_err.log
echo DONE
tail -40 /tmp/tp_ab_fast_err.log
python3 -c "import json; r=open('/tmp/tp_ab_fast_out.json',encoding='utf-8').read(); d=json.loads(r[r.find('{'):]); c=d['comparison']; print('baseline',c['baseline']); print('wide',c['wide']); print('delta_net',round(c['delta_net_profit'],2),'delta_trades',c['delta_trades'],'wide_better',c['wide_better'])"
