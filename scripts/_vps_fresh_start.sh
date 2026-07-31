#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

git pull --ff-only

# Telegram + portfolio caps
grep -q '^TELEGRAM_SIGNAL_DISPATCH=' .env \
  && sed -i 's/^TELEGRAM_SIGNAL_DISPATCH=.*/TELEGRAM_SIGNAL_DISPATCH=true/' .env \
  || echo 'TELEGRAM_SIGNAL_DISPATCH=true' >> .env
grep -q '^PAPER_INITIAL_BALANCE=' .env \
  && sed -i 's/^PAPER_INITIAL_BALANCE=.*/PAPER_INITIAL_BALANCE=5000/' .env \
  || echo 'PAPER_INITIAL_BALANCE=5000' >> .env
grep -q '^PAPER_MAX_PORTFOLIO_RISK_PCT=' .env \
  && sed -i 's/^PAPER_MAX_PORTFOLIO_RISK_PCT=.*/PAPER_MAX_PORTFOLIO_RISK_PCT=30/' .env \
  || echo 'PAPER_MAX_PORTFOLIO_RISK_PCT=30' >> .env
grep -q '^PAPER_MAX_OPEN_POSITIONS=' .env \
  && sed -i 's/^PAPER_MAX_OPEN_POSITIONS=.*/PAPER_MAX_OPEN_POSITIONS=20/' .env \
  || echo 'PAPER_MAX_OPEN_POSITIONS=20' >> .env
grep -q '^PAPER_MAX_OPEN_PER_DIRECTION=' .env \
  && sed -i 's/^PAPER_MAX_OPEN_PER_DIRECTION=.*/PAPER_MAX_OPEN_PER_DIRECTION=12/' .env \
  || echo 'PAPER_MAX_OPEN_PER_DIRECTION=12' >> .env

echo "== env =="
grep -E '^(TELEGRAM_SIGNAL_DISPATCH|PAPER_INITIAL_BALANCE|PAPER_MAX_PORTFOLIO|PAPER_MAX_OPEN)' .env

echo "== build/up =="
docker compose build app worker
docker compose up -d --no-deps app worker

echo "== paper reset =="
docker compose run --rm --no-deps worker python -m app.cli paper reset

echo "== verify settings =="
docker compose run --rm --no-deps worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
print("telegram_dispatch", s.telegram_signal_dispatch)
print("risk_pct", s.paper_max_portfolio_risk_pct)
print("max_open", s.paper_max_open_positions)
print("max_dir", s.paper_max_open_per_direction)
print("initial", s.paper_initial_balance)
assert s.telegram_signal_dispatch is True
assert s.paper_max_portfolio_risk_pct == 30.0
assert s.paper_max_open_positions == 20
assert s.paper_max_open_per_direction == 12
print("OK")
PY

echo "== verify ledger =="
export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT initial_balance, cash_balance, realized_pnl FROM paper_accounts WHERE name='default';"
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) AS positions FROM paper_positions;"

echo "Done."
