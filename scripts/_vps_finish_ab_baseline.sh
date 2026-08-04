#!/usr/bin/env bash
# Finish A/B: snapshot current combo book, rebuild winner baseline onto desk.
set -euo pipefail
APP=/opt/alpha-trade-oracle-bot
cd "$APP"
SINCE="${PAPER_REBUILD_SINCE:-2026-07-31T16:32:35+00:00}"
dc() { docker compose -f "$APP/docker-compose.yml" --env-file "$APP/.env" "$@"; }
set_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then sed -i "s|^${key}=.*|${key}=${val}|" .env
  else echo "${key}=${val}" >> .env; fi
}

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) finish A/B → baseline ====="

docker cp "$APP/scripts/_ab_atr_snapshot.py" alpha-trade-oracle-worker:/app/scripts/_ab_atr_snapshot.py

# Combo is currently on desk from A/B second leg
if grep -q '^ATR_MULTIPLIER=1.8' .env; then
  echo "==> snapshot combo (current desk)"
  dc exec -T worker python /app/scripts/_ab_atr_snapshot.py \
    --label combo --expected-atr 1.8 --expected-near 0.40 --expected-far 1.15 \
    > /tmp/ab_snap_combo.json
  cat /tmp/ab_snap_combo.json
  echo
fi

echo "==> switch geometry to baseline 1.5 / 0.55-1.0"
set_env ATR_MULTIPLIER 1.5
set_env PAPER_RETEST_ZONE_NEAR 0.55
set_env PAPER_RETEST_ZONE_FAR 1.0
set_env PAPER_MAX_OPEN_POSITIONS 40
set_env PAPER_MAX_OPEN_PER_DIRECTION 24
set_env PAPER_REBUILD_RANK_BY_SIM_PNL false

dc up -d --force-recreate --no-deps worker app >/dev/null
sleep 8
docker cp "$APP/app/services/paper_trading_service.py" alpha-trade-oracle-worker:/app/app/services/paper_trading_service.py
docker cp "$APP/app/services/paper_trading_service.py" alpha-trade-oracle-app:/app/app/services/paper_trading_service.py
docker cp "$APP/scripts/_ab_atr_snapshot.py" alpha-trade-oracle-worker:/app/scripts/_ab_atr_snapshot.py
dc restart worker app >/dev/null
sleep 8
dc exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "UPDATE strategy_versions SET atr_multiplier = 1.5 WHERE atr_multiplier IS DISTINCT FROM 1.5;" >/dev/null || true
dc exec -T worker python -c \
  "from app.core.config import get_settings; s=get_settings(); print('live', s.atr_multiplier, s.paper_retest_zone_near, s.paper_retest_zone_far)"

echo "==> rebuild baseline"
dc exec -T worker python -m app.cli paper rebuild \
  --since "$SINCE" --all-signals --all-qualifying 2>&1 | tee /tmp/ab_baseline_restore.log | tail -30

dc exec -T worker python /app/scripts/_ab_atr_snapshot.py \
  --label baseline --expected-atr 1.5 --expected-near 0.55 --expected-far 1.0 \
  > /tmp/ab_snap_baseline.json
cat /tmp/ab_snap_baseline.json
echo

python3 - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path

base = json.loads(Path("/tmp/ab_snap_baseline.json").read_text())
combo = json.loads(Path("/tmp/ab_snap_combo.json").read_text())
payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "since": "2026-07-31T16:32:35+00:00",
    "method": "paper rebuild signal-order + as-of caps/cash; max_open=40 max_per_dir=24; no sim-pnl ranking",
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
        "total_r": round(float(combo.get("total_r", 0)) - float(base.get("total_r", 0)), 3),
    },
    "winner": "baseline" if float(base["equity"]) >= float(combo["equity"]) else "combo",
    "left_on_desk": "baseline",
}
Path("/tmp/ab_atr_asof.json").write_text(json.dumps(payload, indent=2))
print(json.dumps(payload, indent=2))
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) finish A/B done ====="
