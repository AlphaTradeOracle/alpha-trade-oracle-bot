#!/usr/bin/env bash
# Hot-fix: restore signal-order rebuild (a881 path), redeploy code, paper rebuild.
set -euo pipefail
APP=/opt/alpha-trade-oracle-bot
cd "$APP"
export PYTHONPATH=/app
dc() { docker compose -f "$APP/docker-compose.yml" --env-file "$APP/.env" "$@"; }

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) recover signal-order start ====="

python3 - <<'PY'
from pathlib import Path
p = Path("/opt/alpha-trade-oracle-bot/.env")
text = p.read_text(encoding="utf-8") if p.exists() else ""
lines = text.splitlines()
keys = {
    "PAPER_MAX_OPEN_POSITIONS": "40",
    "PAPER_MAX_OPEN_PER_DIRECTION": "24",
    "PAPER_REBUILD_RANK_BY_SIM_PNL": "false",
}
out, seen = [], set()
for line in lines:
    if "=" not in line or line.lstrip().startswith("#"):
        out.append(line)
        continue
    k = line.split("=", 1)[0].strip()
    if k in keys:
        out.append(f"{k}={keys[k]}")
        seen.add(k)
    else:
        out.append(line)
for k, v in keys.items():
    if k not in seen:
        out.append(f"{k}={v}")
p.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
print("env ok", keys)
PY

# Recreate first (image), then docker-cp host code into running containers.
dc up -d --force-recreate --no-deps worker app
sleep 10
for c in alpha-trade-oracle-worker alpha-trade-oracle-app; do
  docker cp "$APP/app/services/paper_trading_service.py" "$c:/app/app/services/paper_trading_service.py"
  docker cp "$APP/app/core/config.py" "$c:/app/app/core/config.py"
done
# Restart so imported modules reload cleanly
dc restart worker app
sleep 8
dc exec -T worker \
  python -c "from app.core.config import get_settings; s=get_settings(); print('caps', s.paper_max_open_positions, s.paper_max_open_per_direction, 'atr', s.atr_multiplier)"
dc exec -T worker python /tmp/_check_rebuild_mode.py 2>/dev/null || \
  dc exec -T worker python -c "import inspect; from app.services.paper_trading_service import PaperTradingService as P; s=inspect.getsource(P._rebuild_from_signal_stream); print('mode', 'two_phase' if 'fill_candidates' in s else 'signal_order')"

SINCE="${PAPER_REBUILD_SINCE:-2026-07-31T16:32:35+00:00}"
echo "===== paper rebuild since $SINCE ====="
dc exec -T worker python -m app.cli paper rebuild \
  --since "$SINCE" \
  --all-signals \
  --all-qualifying

echo "==> desk snapshot"
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import sys, json
p = json.load(sys.stdin).get("portfolio") or {}
print({k: p.get(k) for k in [
  "equity","cash","realizedPnl","closedTrades","openPositions",
  "pendingOrders","winRatePct","totalReturnPct","profitFactor"
]})
'

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) recover signal-order done ====="
