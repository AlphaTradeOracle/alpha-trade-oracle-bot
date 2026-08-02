#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
echo "bot=$(git rev-parse --short HEAD) $(git log -1 --format=%s)"
echo "branch_dash=$(git rev-parse --short origin/cursor/trading-dashboard-efe9 2>/dev/null || echo n/a)"
grep -E '^PAPER_HOURLY_DIGEST_ENABLED=|^MARKET_REGIME_ENABLED=|^INSTITUTIONAL_ENFORCE_GATES=' .env || true
docker compose exec -T worker python -c '
from app.core.config import get_settings
s=get_settings()
print("digest", s.paper_hourly_digest_enabled)
print("regime", s.market_regime_enabled, "enforce", s.institutional_enforce_gates, "short_max", s.signal_short_max_score)
'
stat -c 'static=%y' /var/www/alpha-desk/index.html
grep -l 'marketRegime\|Market Regime' /var/www/alpha-desk/assets/*.js >/tmp/mr_bundle.txt && head -1 /tmp/mr_bundle.txt || echo 'static_NO_marketRegime'
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk.json"))
p=d.get("portfolio") or {}
mr=d.get("marketRegime") or {}
print("equity", p.get("equity"), "realized", p.get("realizedPnl"), "closed", p.get("closedTrades"), "open", p.get("openPositions"))
print("regime_api", mr.get("biasLabel"), "avail", mr.get("available"))
PY
docker compose ps --format '{{.Name}} {{.Status}}'
