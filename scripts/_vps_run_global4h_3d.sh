#!/usr/bin/env bash
# Top400 × 3d paper-parity with Global Score = 100% BTC 4h (no 1h/1d/1w, no ETH).
set -eu
cd /opt/alpha-trade-oracle-bot
set -a; source .env; set +a

export MARKET_REGIME_BTC_TIMEFRAMES=4h
export MARKET_REGIME_ETH_ENABLED=false
export PYTHONUNBUFFERED=1

OUT=exports/top400_global4h_3d.json
mkdir -p exports
rm -f exports/top400_global4h_3d.json exports/top400_paper_parity_90d.partial.json

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) TOP400 3D GLOBAL=4h ====="
echo "BTC_TFS=$MARKET_REGIME_BTC_TIMEFRAMES ETH=$MARKET_REGIME_ETH_ENABLED"

# Bind-mount scripts so we use latest runner; PYTHONPATH=/app for installed app.
# Use compose run worker with enough shared memory / CPUs.
docker compose run --rm --no-deps \
  -e MARKET_REGIME_BTC_TIMEFRAMES=4h \
  -e MARKET_REGIME_ETH_ENABLED=false \
  -e PYTHONUNBUFFERED=1 \
  -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
  -v /opt/alpha-trade-oracle-bot/exports:/app/exports \
  worker python /app/scripts/run_top400_paper_parity_90d.py \
    --top 400 \
    --days 3 \
    --workers 4 \
    --out /app/exports/top400_global4h_3d.json \
  2>&1 | tee exports/top400_global4h_3d.log

echo "===== DONE ====="
ls -la "$OUT" || true
python3 - <<'PY'
import json
from pathlib import Path
p = Path("exports/top400_global4h_3d.json")
if not p.exists():
    print("MISSING", p)
    raise SystemExit(1)
d = json.loads(p.read_text())
print(json.dumps({
    "window": d.get("window"),
    "config_note": (d.get("config") or {}).get("note"),
    "btc_regime": (d.get("config") or {}).get("btc_regime"),
    "kpi": d.get("kpi_paper_book"),
    "independent": d.get("independent"),
    "runtime_s": d.get("runtime_seconds"),
}, indent=2))
PY
