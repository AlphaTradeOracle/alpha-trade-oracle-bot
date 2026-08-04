#!/usr/bin/env bash
# Pin current strategy env, recreate worker/app, wipe paper to $5000, clear cooldowns.
# No historical rebuild — live book starts empty from next scan/fill.
set -euo pipefail
APP=/opt/alpha-trade-oracle-bot
SRC=/tmp/strategy_fresh_deploy
LOG=/tmp/strategy_fresh_start.log
: >"$LOG"
cd "$APP"
dc() { docker compose -f "$APP/docker-compose.yml" --env-file "$APP/.env" "$@"; }

set_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then sed -i "s|^${key}=.*|${key}=${val}|" .env
  else echo "${key}=${val}" >> .env; fi
}

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) strategy fresh start =====" | tee -a "$LOG"

# Optional code sync from /tmp/strategy_fresh_deploy
if [[ -f "$SRC/config.py" ]]; then
  cp "$SRC/config.py" "$APP/app/core/config.py"
fi
if [[ -f "$SRC/paper_trading_service.py" ]]; then
  cp "$SRC/paper_trading_service.py" "$APP/app/services/paper_trading_service.py"
fi
if [[ -f "$SRC/engine.py" ]]; then
  cp "$SRC/engine.py" "$APP/app/signals/engine.py"
fi

# --- Signal generation ---
set_env ENABLE_SCHEDULER true
set_env ENABLE_UNIVERSE_SCAN true
set_env ENABLE_PAPER_TRADING true
set_env ENABLE_LLM_ANALYSIS false
set_env ENABLE_SENTIMENT false
set_env TELEGRAM_SIGNAL_DISPATCH true

set_env SIGNAL_MIN_SCORE 75
set_env SIGNAL_SHORT_MAX_SCORE 30
set_env SIGNAL_SHORT_MIN_SCORE 18
set_env SIGNAL_REQUIRE_STRONG false
set_env MIN_RISK_REWARD_RATIO 2.0
set_env SIGNAL_MIN_ADX 30
set_env SIGNAL_MIN_ADX_SOFT 20
set_env SIGNAL_BLOCK_RANGE_MARKET true
set_env SIGNAL_RSI_LONG_MAX 75
set_env SIGNAL_RSI_SHORT_MIN 33
set_env SIGNAL_COOLDOWN_MINUTES 120
set_env PRIMARY_TIMEFRAME 1h
set_env ATR_MULTIPLIER 1.5
set_env REJECT_WIDE_STOPS false
set_env TP_MULTIPLIERS 1.5,2.5,4.0

set_env REGIME_FILTER_ENABLED true
set_env MARKET_REGIME_ENABLED true
set_env MARKET_REGIME_HARD_VETO true
set_env INSTITUTIONAL_KB_ENABLED true
set_env INSTITUTIONAL_ENFORCE_GATES false

set_env SIGNAL_ENTRY_BLACKOUT_UTC ""
set_env PAPER_ENTRY_BLACKOUT_UTC ""

# --- Paper book ---
set_env PAPER_INITIAL_BALANCE 5000
set_env PAPER_MARGIN_PER_TRADE 300
set_env PAPER_LEVERAGE 10
set_env PAPER_RISK_PER_TRADE_USD 0
set_env PAPER_MAX_NOTIONAL_USD 3000
set_env PAPER_MAX_PORTFOLIO_RISK_PCT 100
set_env PAPER_MAX_OPEN_POSITIONS 16
set_env PAPER_MAX_OPEN_PER_DIRECTION 16
set_env PAPER_MAX_OPEN_PER_DIRECTION_NEUTRAL 8
set_env PAPER_REBUILD_RANK_BY_SIM_PNL false
set_env PAPER_FEE_PERCENT 0.05
set_env PAPER_SCALE_OUT_FRACTIONS 0.5,0.25,0.25
set_env PAPER_RETEST_ENTRY_ENABLED true
set_env PAPER_RETEST_ZONE_NEAR 0.55
set_env PAPER_RETEST_ZONE_FAR 1.0
set_env PAPER_RETEST_PENDING_MULTIPLIER 6
set_env PAPER_RETEST_MIN_BARS_IN_ZONE 1
set_env SIGNAL_TRENDLINE_GATE_ENABLED true
set_env PAPER_HOURLY_DIGEST_ENABLED false
set_env PAPER_UPDATE_INTERVAL_MINUTES 5

set_env UNIVERSE_TARGET_COUNT 400
set_env UNIVERSE_SCAN_BATCH_SIZE 400
set_env SCAN_INTERVAL_MINUTES 15

pkill -f 'app.cli paper rebuild' 2>/dev/null || true
docker ps -q --filter name=worker-run | xargs -r docker rm -f || true

echo "==> recreate worker/app" | tee -a "$LOG"
dc up -d --force-recreate --no-deps worker app >/dev/null
sleep 8
for c in alpha-trade-oracle-worker alpha-trade-oracle-app; do
  [[ -f "$APP/app/core/config.py" ]] && docker cp "$APP/app/core/config.py" "$c:/app/app/core/config.py" || true
  [[ -f "$APP/app/services/paper_trading_service.py" ]] && docker cp "$APP/app/services/paper_trading_service.py" "$c:/app/app/services/paper_trading_service.py" || true
  [[ -f "$APP/app/signals/engine.py" ]] && docker cp "$APP/app/signals/engine.py" "$c:/app/app/signals/engine.py" || true
done
dc restart worker app >/dev/null
sleep 8

echo "==> settings check" | tee -a "$LOG"
dc exec -T worker python - <<'PY' | tee -a "$LOG"
from app.core.config import get_settings
s = get_settings()
cfg = {
    "signal_min": s.signal_min_score,
    "short_max": s.signal_short_max_score,
    "short_min": s.signal_short_min_score,
    "rr": s.min_risk_reward_ratio,
    "atr": s.atr_multiplier,
    "zone": (s.paper_retest_zone_near, s.paper_retest_zone_far),
    "regime": s.regime_filter_enabled,
    "hard_veto": s.market_regime_hard_veto,
    "initial": s.paper_initial_balance,
    "margin": s.paper_margin_per_trade,
    "max_open": s.paper_max_open_positions,
    "max_dir": s.paper_max_open_per_direction,
    "neutral": s.paper_max_open_per_direction_neutral,
    "risk_pct": s.paper_max_portfolio_risk_pct,
    "dispatch": s.telegram_signal_dispatch,
    "llm": s.enable_llm_analysis,
    "universe": s.enable_universe_scan,
}
print(cfg)
assert s.signal_min_score == 75
assert s.signal_short_max_score == 30
assert s.signal_short_min_score == 18
assert s.paper_initial_balance == 5000
assert s.paper_margin_per_trade == 300
assert s.paper_max_open_positions == 16
assert s.paper_max_open_per_direction == 16
assert s.paper_max_open_per_direction_neutral == 8
assert s.paper_max_portfolio_risk_pct == 100
assert s.atr_multiplier == 1.5
assert s.paper_retest_zone_near == 0.55
assert s.paper_retest_zone_far == 1.0
assert s.telegram_signal_dispatch is True
assert s.enable_paper_trading is True
print("SETTINGS_OK")
PY

echo "==> paper reset (no rebuild)" | tee -a "$LOG"
dc run --rm --no-deps worker python -m app.cli paper reset 2>&1 | tee -a "$LOG"

echo "==> clear cooldowns" | tee -a "$LOG"
dc exec -T redis sh -c \
  'redis-cli --scan --pattern "signal:cooldown:*" | while read -r k; do [ -n "$k" ] && redis-cli DEL "$k" >/dev/null; done; echo cooldowns_cleared'

echo "==> ledger verify" | tee -a "$LOG"
dc exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT name, round(initial_balance::numeric,2) AS initial, round(cash_balance::numeric,2) AS cash, round(realized_pnl::numeric,2) AS realized FROM paper_accounts WHERE name='default';" \
  | tee -a "$LOG"
dc exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT status, count(*) FROM paper_positions WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default') GROUP BY 1 ORDER BY 1;" \
  | tee -a "$LOG"

curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_strategy_fresh.json
python3 - <<'PY' | tee -a "$LOG"
import json
from pathlib import Path
p = json.loads(Path("/tmp/desk_strategy_fresh.json").read_text()).get("portfolio") or {}
print("desk", {k: p.get(k) for k in [
    "equity", "cash", "realizedPnl", "closedTrades",
    "openPositions", "pendingOrders", "totalReturnPct", "winRatePct",
]})
assert float(p.get("equity") or 0) == 5000.0
assert int(p.get("openPositions") or 0) == 0
assert int(p.get("pendingOrders") or 0) == 0
assert int(p.get("closedTrades") or 0) == 0
print("LEDGER_OK")
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) strategy fresh start DONE =====" | tee -a "$LOG"
