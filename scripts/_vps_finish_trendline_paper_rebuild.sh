#!/usr/bin/env bash
# Stop bloated all-signals rebuild; rebuild paper on reset allowlist with trendline gate.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

SINCE="${SINCE:-2026-07-31T16:32:35+00:00}"
SYMBOLS_FILE="${SYMBOLS_FILE:-scripts/paper_reset_symbols.txt}"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) finish trendline paper rebuild ====="

echo "==> stop running all-signals paper rebuild"
pkill -f 'app.cli paper rebuild' 2>/dev/null || true
pkill -f '_vps_deploy_trendline_rebuild' 2>/dev/null || true
sleep 2
# stop docker compose run containers doing paper rebuild
while read -r id; do
  [[ -z "$id" ]] && continue
  cmd="$(docker inspect -f '{{json .Config.Cmd}}' "$id" 2>/dev/null || true)"
  if echo "$cmd" | grep -q 'paper rebuild'; then
    echo "stopping $id $cmd"
    docker stop "$id" >/dev/null || true
  fi
done < <(docker ps -q)
sleep 1
pgrep -af 'paper rebuild' || echo "paper rebuild stopped"

# Ensure code + env
git pull --ff-only origin main || true
ensure_env() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" .env 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    printf '\n%s=%s\n' "$key" "$val" >> .env
  fi
}
ensure_env SIGNAL_TRENDLINE_GATE_ENABLED true
ensure_env SIGNAL_TRENDLINE_LOOKBACK 40
ensure_env SIGNAL_TRENDLINE_BUFFER_ATR 0.1
ensure_env SIGNAL_TRENDLINE_MIN_POINTS 2
ensure_env SIGNAL_TRENDLINE_MIN_R2 0.85
ensure_env SIGNAL_TRENDLINE_MIN_CLEARANCE_ATR 0.0
grep -E '^SIGNAL_TRENDLINE_' .env

# Worker/app should already be rebuilt; ensure up
docker compose up -d --no-deps worker app

echo "==> verify settings"
docker compose run --rm --no-deps worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
assert s.signal_trendline_gate_enabled
print("gate", s.signal_trendline_gate_enabled, "buffer", s.signal_trendline_buffer_atr, "lookback", s.signal_trendline_lookback)
PY

export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"

echo "==> BASELINE"
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A <<'SQL'
SELECT 'BASELINE|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default' GROUP BY status ORDER BY status;
SELECT 'ACCT|' || COALESCE(ROUND(realized_pnl::numeric,2),0) || '|' || COALESCE(ROUND(cash_balance::numeric,2),0)
FROM paper_accounts WHERE name='default';
SQL

echo "==> paper rebuild allowlist since=${SINCE} file=${SYMBOLS_FILE}"
wc -l "$SYMBOLS_FILE"
docker compose run --rm --no-deps worker \
  python -m app.cli paper rebuild \
  --since "${SINCE}" \
  --all-signals \
  --symbols-file "${SYMBOLS_FILE}"

echo "==> AFTER"
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A <<'SQL'
SELECT 'AFTER|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default' GROUP BY status ORDER BY status;
SELECT 'ACCT|' || COALESCE(ROUND(realized_pnl::numeric,2),0) || '|' || COALESCE(ROUND(cash_balance::numeric,2),0)
FROM paper_accounts WHERE name='default';
SELECT 'TL_SKIP|' || COUNT(*)
FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default'
  AND (p.notes ILIKE '%broke_falling%' OR p.notes ILIKE '%broke_rising%' OR p.notes ILIKE '%too_close_%');
SELECT 'SYM|' || symbol || '|' || status || '|' || ROUND(COALESCE(realized_pnl,0)::numeric,2)
  || '|' || COALESCE(exit_reason,'') || '|' || LEFT(COALESCE(notes,''),100)
FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default' AND p.status IN ('open','closed','pending')
ORDER BY opened_at NULLS LAST;
SQL

sleep 2
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_after_tl.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk_after_tl.json"))
p=d.get("portfolio") or {}
print("DESK equity", p.get("equity"), "realized", p.get("realizedPnl"),
      "closed", p.get("closedTrades"), "open", p.get("openPositions"),
      "pending", p.get("pendingOrders"))
for t in (d.get("trades") or [])[:40]:
    print(t.get("symbol"), t.get("status"), t.get("realizedPnl") or t.get("pnl"), t.get("notes"))
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) finish trendline paper rebuild done ====="
