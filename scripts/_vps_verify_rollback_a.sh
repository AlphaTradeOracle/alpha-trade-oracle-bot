#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
echo "=== HEAD $(git rev-parse --short HEAD) $(git log -1 --format=%s) ==="
echo "=== containers ==="
docker compose ps
echo "=== worker code ==="
docker compose exec -T worker python - <<'PY'
import importlib.util
from app.core.config import get_settings
s = get_settings()
print("trendlines", importlib.util.find_spec("app.indicators.trendlines"))
print("retest", s.paper_retest_entry_enabled, "short_max", s.signal_short_max_score)
print("has_tl", hasattr(s, "signal_trendline_gate_enabled"))
PY
echo "=== paper account + book ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
\d paper_accounts
SELECT id, name, starting_balance, cash_balance, realized_pnl, updated_at
FROM paper_accounts WHERE name = 'default';
SELECT status, count(*) FROM paper_positions
WHERE account_id = (SELECT id FROM paper_accounts WHERE name = 'default')
GROUP BY 1 ORDER BY 1;
SELECT symbol, direction, status, opened_at, entry_price
FROM paper_positions
WHERE account_id = (SELECT id FROM paper_accounts WHERE name = 'default')
  AND status IN ('open','pending','pending_retest')
ORDER BY opened_at;
SELECT symbol, count(*) AS n
FROM paper_positions
WHERE account_id = (SELECT id FROM paper_accounts WHERE name = 'default')
  AND status = 'closed'
  AND symbol IN ('CVCUSDT','TREEUSDT','PHAUSDT','QNTUSDT','REDUSDT','BATUSDT','MOCAUSDT')
GROUP BY 1 ORDER BY 1;
SQL
echo "=== API ==="
curl -sS http://127.0.0.1:8000/health || true
echo
curl -sS http://127.0.0.1:8000/api/paper/summary 2>/dev/null | head -c 800 || \
curl -sS http://127.0.0.1:8000/paper/summary 2>/dev/null | head -c 800 || \
curl -sS http://127.0.0.1:8000/api/v1/paper/account 2>/dev/null | head -c 800 || true
echo
# kill leftover wait loop from old rebuild
pkill -f 'finish_trendline_paper_rebuild' 2>/dev/null || true
echo "=== DONE ==="
