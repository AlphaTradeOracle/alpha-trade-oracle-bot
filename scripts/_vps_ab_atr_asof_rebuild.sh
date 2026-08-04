#!/usr/bin/env bash
# A/B: Baseline ATR1.5/zone0.55-1.0 vs Combo ATR1.8/zone0.40-1.15
# under current signal-order + as-of caps/cash paper rebuild.
set -euo pipefail
APP=/opt/alpha-trade-oracle-bot
cd "$APP"
SINCE="${PAPER_REBUILD_SINCE:-2026-07-31T16:32:35+00:00}"
OUT="${AB_OUT:-/tmp/ab_atr_asof.json}"
LOG="${AB_LOG:-/tmp/ab_atr_asof.log}"
: >"$LOG"
dc() { docker compose -f "$APP/docker-compose.yml" --env-file "$APP/.env" "$@"; }

set_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}

apply_geometry() {
  local atr="$1" near="$2" far="$3" label="$4"
  echo "==> geometry $label atr=$atr zone=$near-$far" | tee -a "$LOG"
  set_env ATR_MULTIPLIER "$atr"
  set_env PAPER_RETEST_ZONE_NEAR "$near"
  set_env PAPER_RETEST_ZONE_FAR "$far"
  set_env PAPER_MAX_OPEN_POSITIONS 40
  set_env PAPER_MAX_OPEN_PER_DIRECTION 24
  set_env PAPER_REBUILD_RANK_BY_SIM_PNL false

  dc up -d --force-recreate --no-deps worker app >/dev/null
  sleep 8
  docker cp "$APP/app/services/paper_trading_service.py" \
    alpha-trade-oracle-worker:/app/app/services/paper_trading_service.py
  docker cp "$APP/app/services/paper_trading_service.py" \
    alpha-trade-oracle-app:/app/app/services/paper_trading_service.py
  docker cp "$APP/scripts/_ab_atr_snapshot.py" \
    alpha-trade-oracle-worker:/app/scripts/_ab_atr_snapshot.py
  dc restart worker app >/dev/null
  sleep 8

  dc exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
    "UPDATE strategy_versions SET atr_multiplier = ${atr} WHERE atr_multiplier IS DISTINCT FROM ${atr};" \
    >/dev/null || true

  dc exec -T worker python -c \
    "from app.core.config import get_settings; s=get_settings(); print('live', s.atr_multiplier, s.paper_retest_zone_near, s.paper_retest_zone_far, 'caps', s.paper_max_open_positions, s.paper_max_open_per_direction)" \
    | tee -a "$LOG"
}

snapshot_variant() {
  local label="$1" atr="$2" near="$3" far="$4"
  local tmp="/tmp/ab_snap_${label}.json"
  echo "==> rebuild $label since $SINCE" | tee -a "$LOG"
  dc exec -T worker python -m app.cli paper rebuild \
    --since "$SINCE" \
    --all-signals \
    --all-qualifying \
    2>&1 | tee -a "$LOG"

  dc exec -T worker python /app/scripts/_ab_atr_snapshot.py \
    --label "$label" \
    --expected-atr "$atr" \
    --expected-near "$near" \
    --expected-far "$far" \
    | tee "$tmp" | tee -a "$LOG"
  echo "$tmp"
}

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) A/B ATR as-of start =====" | tee -a "$LOG"

apply_geometry 1.5 0.55 1.0 baseline
BASE_FILE=$(snapshot_variant baseline 1.5 0.55 1.0)

apply_geometry 1.8 0.40 1.15 combo
COMBO_FILE=$(snapshot_variant combo 1.8 0.40 1.15)

python3 - <<PY | tee "$OUT"
import json
from pathlib import Path
from datetime import datetime, timezone

base = json.loads(Path("$BASE_FILE").read_text())
combo = json.loads(Path("$COMBO_FILE").read_text())

def key(r):
    return (
        float(r["equity"]),
        float(r["win_rate_pct"]),
        -int(r["peak_concurrent_filled"]),
    )

winner = "combo" if key(combo) >= key(base) else "baseline"
payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "since": "$SINCE",
    "method": (
        "paper rebuild signal-order + as-of caps/cash; "
        "max_open=40 max_per_dir=24; no sim-pnl ranking"
    ),
    "baseline": base,
    "combo": combo,
    "delta_combo_minus_baseline": {
        "equity": round(float(combo["equity"]) - float(base["equity"]), 2),
        "closed_trades": int(combo["closed_trades"]) - int(base["closed_trades"]),
        "win_rate_pct": round(float(combo["win_rate_pct"]) - float(base["win_rate_pct"]), 1),
        "peak_concurrent_filled": int(combo["peak_concurrent_filled"])
        - int(base["peak_concurrent_filled"]),
        "total_return_pct": round(
            float(combo["total_return_pct"]) - float(base["total_return_pct"]), 2
        ),
    },
    "winner": winner,
    "left_on_desk": "combo",
}
print(json.dumps(payload, indent=2, default=str))
PY

WINNER=$(python3 -c "import json; print(json.load(open('$OUT'))['winner'])")
if [[ "$WINNER" == "baseline" ]]; then
  echo "==> baseline won — restoring baseline on desk" | tee -a "$LOG"
  apply_geometry 1.5 0.55 1.0 baseline
  snapshot_variant baseline 1.5 0.55 1.0 >/dev/null
  python3 - <<'PY'
import json
from pathlib import Path
p = Path("/tmp/ab_atr_asof.json")
d = json.loads(p.read_text())
d["left_on_desk"] = "baseline"
p.write_text(json.dumps(d, indent=2, default=str))
print("left_on_desk=baseline")
PY
else
  echo "==> combo kept on desk" | tee -a "$LOG"
fi

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) A/B ATR as-of done winner=$WINNER =====" | tee -a "$LOG"
echo "OUT=$OUT"
