#!/usr/bin/env bash
# Top400 × 3d · Global Score BTC weights: 1h 20% / 4h 30% / 1d 50%
set -eu
cd /opt/alpha-trade-oracle-bot
set -a; source .env; set +a
export PYTHONUNBUFFERED=1

OUT=exports/top400_global_w_1h20_4h30_1d50_3d.json
mkdir -p exports
rm -f "$OUT" exports/top400_paper_parity_90d.partial.json

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) TOP400 3D weights 1h20/4h30/1d50 ====="

docker compose run --rm --no-deps \
  -e PYTHONUNBUFFERED=1 \
  -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
  -v /opt/alpha-trade-oracle-bot/exports:/app/exports \
  worker python /app/scripts/run_top400_paper_parity_90d.py \
    --top 400 \
    --days 3 \
    --workers 4 \
    --btc-tfs 1h,4h,1d \
    --btc-weights 1h:0.2,4h:0.3,1d:0.5 \
    --label top400_3d_global_1h20_4h30_1d50 \
    --out /app/exports/top400_global_w_1h20_4h30_1d50_3d.json \
  2>&1 | tee exports/top400_global_w_1h20_4h30_1d50_3d.log

echo "===== DONE ====="
python3 - <<'PY'
import json
from pathlib import Path
p = Path("exports/top400_global_w_1h20_4h30_1d50_3d.json")
d = json.loads(p.read_text())
print(json.dumps({
    "label": d.get("label"),
    "window": d.get("window"),
    "weights": (d.get("config") or {}).get("btc_regime_weights"),
    "kpi": d.get("kpi_paper_book"),
    "independent": d.get("independent"),
    "runtime_s": d.get("runtime_seconds"),
}, indent=2))
PY
