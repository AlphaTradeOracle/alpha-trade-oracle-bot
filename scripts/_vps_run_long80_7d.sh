#!/usr/bin/env bash
# Deploy engine patch + run 7D long>=80 backtest on top-100 universe.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) long>=80 7d backtest ====="

# Sync needed files from the scp'd copies if present, else expect git
if [[ -f /tmp/backtest_long_min80_7d.py ]]; then
  cp -f /tmp/backtest_long_min80_7d.py scripts/backtest_long_min80_7d.py
fi
if [[ -f /tmp/engine_long_min.py ]]; then
  cp -f /tmp/engine_long_min.py app/backtesting/engine.py
fi

mkdir -p exports
OUT=exports/long_min80_7d.json
LOG=exports/long_min80_7d.log

# Copy into running worker image context via bind — scripts/ is in the project dir
# Rebuild worker quickly so engine change is in the image, OR mount via compose run.
docker compose build worker
docker compose run --rm --no-deps worker \
  python scripts/backtest_long_min80_7d.py \
  --top 100 --days 7 --prefer-db \
  --out "$OUT" \
  2>&1 | tee "$LOG"

echo "===== done ====="
ls -la "$OUT"
python3 - <<'PY'
import json
p=json.load(open("exports/long_min80_7d.json"))
print(json.dumps(p["summary"], indent=2))
print("top_long_symbols", p.get("top_long_symbols", [])[:5])
PY
