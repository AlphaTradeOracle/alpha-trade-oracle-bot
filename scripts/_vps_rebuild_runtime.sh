#!/usr/bin/env bash
# Full image rebuild so pip-installed site-packages matches git (docker cp to /app/app is a no-op for imports).
# Does NOT reset the paper ledger.
set -euo pipefail
APP=/opt/alpha-trade-oracle-bot
SRC=/tmp/rebuild_runtime_deploy
LOG=/tmp/deploy_rebuild_runtime.log
: >"$LOG"
cd "$APP"
dc() { docker compose -f "$APP/docker-compose.yml" --env-file "$APP/.env" "$@"; }

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) rebuild runtime =====" | tee -a "$LOG"

# Optional scp bundle — only when explicitly newer than git tree.
# Never blindly overwrite git-checked files with a stale /tmp bundle (caused
# live builds to miss market_regime_fail_closed after git reset --hard).
if [[ "${REBUILD_USE_SRC_BUNDLE:-0}" == "1" && -d "$SRC" ]]; then
  [[ -f "$SRC/paper_repository.py" ]] && cp "$SRC/paper_repository.py" "$APP/app/repositories/paper_repository.py"
  [[ -f "$SRC/paper_trading_service.py" ]] && cp "$SRC/paper_trading_service.py" "$APP/app/services/paper_trading_service.py"
  [[ -f "$SRC/config.py" ]] && cp "$SRC/config.py" "$APP/app/core/config.py"
  [[ -f "$SRC/docker-compose.yml" ]] && cp "$SRC/docker-compose.yml" "$APP/docker-compose.yml"
fi

# Ensure realism + book env keys exist (idempotent).
set_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then sed -i "s|^${key}=.*|${key}=${val}|" .env
  else echo "${key}=${val}" >> .env; fi
}
set_env PAPER_SLIPPAGE_PERCENT 0.05
set_env PAPER_FUNDING_ENABLED true
set_env PAPER_FUNDING_INTERVAL_HOURS 8
set_env PAPER_FUNDING_RATE_DEFAULT 0.0001
set_env PAPER_MAX_OPEN_POSITIONS 16
set_env PAPER_MAX_OPEN_PER_DIRECTION 16
set_env PAPER_MAX_OPEN_PER_DIRECTION_NEUTRAL 8
set_env PAPER_MAX_PORTFOLIO_RISK_PCT 100
set_env PAPER_RETEST_ZONE_NEAR 0.55
set_env PAPER_RETEST_ZONE_FAR 1.0
set_env PAPER_RETEST_PENDING_MULTIPLIER 6
set_env ATR_MULTIPLIER 1.5
set_env SIGNAL_MIN_SCORE 75
set_env SIGNAL_SHORT_MAX_SCORE 30
set_env SIGNAL_SHORT_MIN_SCORE 18
set_env MIN_RISK_REWARD_RATIO 2.0
set_env MARKET_REGIME_HARD_VETO true
set_env MARKET_REGIME_FAIL_CLOSED true
set_env REGIME_FILTER_ENABLED true
set_env INSTITUTIONAL_ENFORCE_GATES true

echo "Building app+worker images (no cache for app layer)..." | tee -a "$LOG"
dc build --no-cache app worker 2>&1 | tee -a "$LOG"
echo "Recreating app+worker..." | tee -a "$LOG"
dc up -d --force-recreate app worker 2>&1 | tee -a "$LOG"
sleep 12

dc exec -T worker python - <<'PY' | tee -a "$LOG"
import inspect
import app.repositories.paper_repository as r
import app.services.paper_trading_service as p
import app.core.config as c
from app.core.config import get_settings

s = get_settings()
print("repo", r.__file__)
print("paper", p.__file__)
print("config", c.__file__)
# Runtime may import from /app/app (WORKDIR) or site-packages after pip install.
assert "/app/" in r.__file__ or "site-packages" in r.__file__
assert hasattr(r.PaperRepository, "lock_account")
assert "with_for_update" in inspect.getsource(r.PaperRepository.lock_account)
src = inspect.getsource(p.PaperTradingService)
assert "_slip_price" in src
assert "_ledger_lock" in src
assert "paper_max_open_per_direction_neutral" in src
assert float(s.paper_slippage_percent) == 0.05
assert s.paper_funding_enabled is True
assert int(s.paper_max_open_per_direction_neutral) == 8
assert int(s.paper_max_open_positions) == 16
assert int(s.paper_max_open_per_direction) == 16
assert float(s.signal_min_score) == 75.0
assert float(s.signal_short_max_score) == 30.0
assert float(s.signal_short_min_score) == 18.0
assert float(s.min_risk_reward_ratio) == 2.0
assert float(s.atr_multiplier) == 1.5
assert float(s.paper_retest_zone_near) == 0.55
assert float(s.paper_retest_zone_far) == 1.0
assert s.market_regime_fail_closed is True
assert s.institutional_enforce_gates is True
print("RUNTIME_OK")
print({
    "slip": s.paper_slippage_percent,
    "funding": s.paper_funding_enabled,
    "neutral": s.paper_max_open_per_direction_neutral,
    "max_open": s.paper_max_open_positions,
    "fail_closed": s.market_regime_fail_closed,
    "enforce_gates": s.institutional_enforce_gates,
    "lock": True,
})
PY

curl -fsS http://127.0.0.1:8000/health | tee -a "$LOG"
echo | tee -a "$LOG"
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_rebuild.json
python3 - <<'PY' | tee -a "$LOG"
import json
from pathlib import Path
p=json.loads(Path("/tmp/desk_rebuild.json").read_text()).get("portfolio") or {}
print("desk", {k:p.get(k) for k in ["equity","cash","openPositions","pendingOrders","closedTrades"]})
PY

# Bind check still localhost-only for DB/cache
ss -lnt | grep -E ':(5432|6379|8000)\b' | tee -a "$LOG" || true

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) rebuild runtime DONE =====" | tee -a "$LOG"
