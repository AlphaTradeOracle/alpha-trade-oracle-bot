#!/usr/bin/env bash
set -euo pipefail
echo "=== candles BTCUSDT 1h ==="
curl -fsS "http://127.0.0.1:8000/api/v1/desk/candles?symbol=BTCUSDT&interval=1h&from=1754000000&to=1754086400" \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print("n",len(d));print(d[0] if d else None)'
echo "=== equity points ==="
curl -fsS "http://127.0.0.1:8000/api/v1/desk/snapshot" \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);e=d["equity"];print("n",len(e));print("first",e[0]);print("last",e[-1])'
echo "=== public candles via nginx ==="
curl -fsS "https://www.alpha-trade-oracle.com/api/v1/desk/candles?symbol=ETHUSDT&interval=1h&from=1754000000&to=1754086400" \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print("n",len(d))'
