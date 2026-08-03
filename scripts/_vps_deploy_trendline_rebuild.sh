#!/usr/bin/env bash
# Deploy trendline gate + rebuild paper ledger since reset with new strategy.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

SINCE="${SINCE:-2026-07-31T16:32:35+00:00}"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy trendline + paper rebuild ====="
echo "since=${SINCE}"

git fetch origin
git checkout main
git pull --ff-only origin main
git log -1 --oneline

# Ensure SIGNAL_TRENDLINE_* in .env (defaults match Settings).
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
grep -E '^SIGNAL_TRENDLINE_' .env || true

echo "==> BASELINE KPIs"
export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A <<'SQL'
SELECT 'BASELINE|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default' GROUP BY status ORDER BY status;
SELECT 'ACCT|' || COALESCE(ROUND(realized_pnl::numeric,2),0) || '|' || COALESCE(ROUND(cash_balance::numeric,2),0)
FROM paper_accounts WHERE name='default';
SQL

echo "==> build + restart worker/app"
docker compose build worker app
docker compose up -d --no-deps worker app

echo "==> verify trendline settings in container"
docker compose run --rm --no-deps worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
print("gate", s.signal_trendline_gate_enabled)
print("lookback", s.signal_trendline_lookback)
print("buffer", s.signal_trendline_buffer_atr)
print("min_points", s.signal_trendline_min_points)
print("min_r2", s.signal_trendline_min_r2)
assert s.signal_trendline_gate_enabled is True
print("OK")
PY

echo "==> paper rebuild (all qualifying signals since reset; new trendline gate)"
docker compose run --rm --no-deps worker \
  python -m app.cli paper rebuild \
  --since "${SINCE}" \
  --all-signals

echo "==> AFTER KPIs"
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A <<'SQL'
SELECT 'AFTER|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default' GROUP BY status ORDER BY status;
SELECT 'WR|' || wins || '|' || losses || '|' || ROUND(wr*100,1) || '|' || pf
FROM (
  SELECT
    COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
    COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses,
    CASE WHEN COUNT(*)>0 THEN COUNT(*) FILTER (WHERE realized_pnl > 0)::float/COUNT(*) ELSE 0 END AS wr,
    CASE WHEN ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0),0)) > 0
      THEN ROUND((COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl > 0),0) / ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0),0)))::numeric,2)
      ELSE 999 END AS pf
  FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
  WHERE a.name='default' AND p.status='closed'
) s;
SELECT 'ACCT|' || COALESCE(ROUND(realized_pnl::numeric,2),0) || '|' || COALESCE(ROUND(cash_balance::numeric,2),0)
FROM paper_accounts WHERE name='default';
SELECT 'TL_SKIP|' || COUNT(*)
FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default'
  AND (p.notes ILIKE '%broke_%resistance%' OR p.notes ILIKE '%broke_%support%'
       OR p.notes ILIKE '%broke_falling%' OR p.notes ILIKE '%broke_rising%');
SELECT 'SYM|' || symbol || '|' || status || '|' || ROUND(COALESCE(realized_pnl,0)::numeric,2) || '|' || COALESCE(exit_reason,'') || '|' || LEFT(COALESCE(notes,''),80)
FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default'
ORDER BY opened_at NULLS LAST;
SQL

sleep 2
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_after_tl.json || true
python3 - <<'PY'
import json
from pathlib import Path
p = Path("/tmp/desk_after_tl.json")
if not p.exists():
    print("DESK_SNAP missing")
    raise SystemExit(0)
d = json.load(p.open())
port = d.get("portfolio") or {}
print(
    "DESK",
    "equity", port.get("equity"),
    "realized", port.get("realizedPnl"),
    "closed", port.get("closedTrades"),
    "open", port.get("openPositions"),
    "pending", port.get("pendingOrders"),
)
trades = d.get("trades") or []
print("desk_trades", len(trades))
for t in trades[:30]:
    print(
        t.get("symbol"),
        t.get("status"),
        t.get("realizedPnl") or t.get("pnl"),
        t.get("notes"),
    )
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy trendline + paper rebuild done ====="
