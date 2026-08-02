#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
git fetch origin main
git reset --hard origin/main
docker compose build worker
docker compose up -d --no-deps worker
mkdir -p exports
docker compose run --rm --no-deps worker \
  python scripts/backtest_short_min_floor.py \
  --top 50 --days 7 --no-mtf --prefer-db \
  --out exports/short_min_floor_7d_top50.json \
  2> exports/short_min_floor_7d_top50.log
echo EXIT:$?
tail -80 exports/short_min_floor_7d_top50.log
python3 - <<'PY'
import json
p=json.load(open("exports/short_min_floor_7d_top50.json"))
print("VERDICT", p.get("verdict"))
for s in p["summaries"]:
    print(s)
print("MARGINAL", p["marginal_vs_floor_18"])
PY
