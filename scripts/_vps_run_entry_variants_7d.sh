#!/usr/bin/env bash
# Launch Top400×7d entry-variant sim inside the worker container (detached).
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/alpha-trade-oracle-bot}"
OUT="${OUT:-/tmp/entry_variants_top100_7d.json}"
LOG="${LOG:-/tmp/entry_variants_top100_7d.log}"
WORKERS="${WORKERS:-2}"
DAYS="${DAYS:-7}"
TOP="${TOP:-100}"
SERVICE="${SERVICE:-worker}"

cd "$APP_DIR"
CID=$(docker compose ps -q "$SERVICE")
if [[ -z "$CID" ]]; then
  echo "$SERVICE container not running" >&2
  exit 1
fi

docker cp "$APP_DIR/app/backtesting/engine.py" "$CID:/app/app/backtesting/engine.py"
docker cp "$APP_DIR/scripts/run_top400_paper_parity_90d.py" "$CID:/app/scripts/run_top400_paper_parity_90d.py"
docker cp "$APP_DIR/scripts/run_entry_variants_top400_7d.py" "$CID:/app/scripts/run_entry_variants_top400_7d.py"

docker compose exec -T "$SERVICE" python -c "import pydantic; from app.backtesting.engine import BacktestConfig; print('ok', BacktestConfig(symbol='X',timeframe='1h',entry_mode='hybrid_chase').entry_mode)"

docker compose exec -T "$SERVICE" python - <<'PY' || true
import os, signal
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode()
    except Exception:
        continue
    if "run_entry_variants_top400_7d.py" in cmd:
        os.kill(int(pid), signal.SIGTERM)
        print("killed", pid)
PY

docker compose exec -d "$SERVICE" bash -c \
  "python -u /app/scripts/run_entry_variants_top400_7d.py --top ${TOP} --days ${DAYS} --workers ${WORKERS} --out ${OUT} > ${LOG} 2>&1"

sleep 5
echo "OUT=${OUT} LOG=${LOG} SERVICE=${SERVICE}"
docker compose exec -T "$SERVICE" python /app/scripts/_vps_entry_variants_status.py "$(basename "$OUT" .json)"
