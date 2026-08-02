#!/usr/bin/env bash
# Full health: telegram, DB paper, website/API.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) FULL HEALTH ====="
echo "== GIT =="
echo "bot_head=$(git rev-parse --short HEAD) $(git log -1 --format=%s)"
git status -sb | head -5
git status --porcelain | head -20 || true

echo "== ENV KEYS =="
grep -E '^(PAPER_HOURLY_DIGEST_ENABLED|MARKET_REGIME_ENABLED|MARKET_REGIME_HARD_VETO|INSTITUTIONAL_KB_ENABLED|INSTITUTIONAL_ENFORCE_GATES|SIGNAL_SHORT_MAX_SCORE|ENABLE_PAPER_TRADING)=' .env || true

echo "== RUNTIME SETTINGS =="
docker compose exec -T worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
print("digest_enabled", s.paper_hourly_digest_enabled)
print("paper_enabled", s.enable_paper_trading)
print("regime", s.market_regime_enabled, "hard_veto", s.market_regime_hard_veto)
print("ikb", s.institutional_kb_enabled, "enforce", s.institutional_enforce_gates)
print("short_max", s.signal_short_max_score)
print("allowed_chats", s.telegram_allowed_chat_ids)
print("token_set", bool(s.telegram_bot_token.get_secret_value()))
PY

echo "== TELEGRAM DIGEST GATE (source) =="
docker compose exec -T worker python - <<'PY'
from pathlib import Path
src = Path("/app/app/scheduler/runner.py").read_text()
print("digest_gated_by_flag", "paper_hourly_digest_enabled" in src)
print("digest_job_import", "paper_digest_job" in src)
PY

echo "== TELEGRAM BOT GETME =="
docker compose exec -T worker python - <<'PY'
import asyncio
from telegram import Bot
from app.core.config import get_settings
async def main():
    s = get_settings()
    bot = Bot(s.telegram_bot_token.get_secret_value())
    me = await bot.get_me()
    chats = s.telegram_allowed_chat_ids or []
    print("bot_ok", me.username, me.id, "allowed", len(chats))
asyncio.run(main())
PY

echo "== WORKER LOG (recent telegram/errors) =="
docker compose logs worker --since 90m 2>/dev/null | grep -iE 'error|exception|digest|Application started|getUpdates|Conflict' | tail -25 || true

echo "== DOCKER =="
docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Service}}'

export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
export PGPASSWORD

echo "== DB PAPER =="
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
\pset tuples_only on
\pset format unaligned
SELECT 'STATUS|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions GROUP BY status ORDER BY status;
SELECT 'ACCT|' || COALESCE(ROUND(realized_pnl::numeric,2),0) || '|' || COALESCE(ROUND(cash_balance::numeric,2),0) || '|' || COALESCE(ROUND(initial_balance::numeric,2),0)
FROM paper_accounts WHERE name='default';
SELECT 'SYM|' || symbol || '|' || status || '|' || ROUND(COALESCE(realized_pnl,0)::numeric,2) || '|' || COALESCE(exit_reason,'-')
FROM paper_positions ORDER BY opened_at;
SQL

echo "== SYMBOL ALLOWLIST CHECK =="
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A -c \
  "SELECT DISTINCT symbol FROM paper_positions ORDER BY 1;" > /tmp/paper_syms.txt
if [[ -f scripts/paper_reset_symbols.txt ]]; then
  comm -23 <(sort /tmp/paper_syms.txt) <(grep -vE '^\s*(#|$)' scripts/paper_reset_symbols.txt | sort) > /tmp/extra_syms.txt || true
  if [[ -s /tmp/extra_syms.txt ]]; then
    echo "FAIL extra symbols not in allowlist:"
    cat /tmp/extra_syms.txt
  else
    echo "OK all paper symbols in allowlist"
  fi
else
  echo "WARN no scripts/paper_reset_symbols.txt on disk"
fi
echo "paper_symbols=$(wc -l </tmp/paper_syms.txt)"

echo "== API / DESK =="
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk.json"))
p=d.get("portfolio") or {}
mr=d.get("marketRegime") or {}
closed=p.get("closedTrades") or 0
print("DESK equity", p.get("equity"), "realized", p.get("realizedPnl"),
      "closed", closed, "open", p.get("openPositions"),
      "pending", p.get("pendingOrders"), "cash", p.get("cash"))
print("REGIME", mr.get("biasLabel"), "avail", mr.get("available"))
print("CLOSED_SANITY", "OK" if closed < 30 else "FAIL_TOO_HIGH", closed)
print("NOT_FLOOD", "OK" if closed != 68 else "FAIL_STILL_68")
PY

echo "== WEBSITE STATIC =="
stat -c 'index=%y' /var/www/alpha-desk/index.html
BUNDLE=$(ls -1 /var/www/alpha-desk/assets/index-*.js 2>/dev/null | head -1 || true)
if [[ -n "$BUNDLE" ]] && grep -q 'marketRegime\|Market Regime' "$BUNDLE"; then
  echo "OK bundle has marketRegime: $(basename "$BUNDLE")"
else
  echo "FAIL bundle missing marketRegime"
fi
curl -fsS -o /dev/null -w "local_http=%{http_code}\n" http://127.0.0.1:8000/api/v1/desk/snapshot || true
# try common public hosts
for u in https://desk.alpha-trade-oracle.com/ https://alpha-trade-oracle.com/; do
  code=$(curl -fsS -o /dev/null -w "%{http_code}" --connect-timeout 5 "$u" 2>/dev/null || echo fail)
  echo "public $u -> $code"
done

echo "===== HEALTH DONE ====="
