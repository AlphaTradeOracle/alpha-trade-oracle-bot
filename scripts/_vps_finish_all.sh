#!/usr/bin/env bash
# 1) live strategy probe  2) db audit  3) data prune  4) post-check
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) FINISH ALL ====="
echo "bot=$(git rev-parse --short HEAD)"
git fetch origin main cursor/trading-dashboard-efe9
git reset --hard origin/main

# ensure soft-blend env
upsert_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then sed -i "s|^${key}=.*|${key}=${val}|" .env
  else echo "${key}=${val}" >> .env; fi
}
upsert_env MARKET_REGIME_ENABLED true
upsert_env MARKET_REGIME_HARD_VETO true
upsert_env INSTITUTIONAL_KB_ENABLED true
upsert_env INSTITUTIONAL_ENFORCE_GATES false
upsert_env SIGNAL_SHORT_MAX_SCORE 25
upsert_env PAPER_HOURLY_DIGEST_ENABLED false

docker compose build worker app
docker compose up -d --no-deps worker app

echo "==> LIVE STRATEGY PROBE"
docker compose exec -T worker python - <<'PY'
import asyncio
from sqlalchemy import text
from app.container import build_container
from app.core.config import get_settings
from app.database.session import session_scope

async def main():
    s = get_settings()
    print("flags",
          "regime", s.market_regime_enabled,
          "veto", s.market_regime_hard_veto,
          "ikb", s.institutional_kb_enabled,
          "enforce", s.institutional_enforce_gates,
          "short_max", s.signal_short_max_score,
          "dispatch", s.telegram_signal_dispatch)
    c = build_container(s)
    async with session_scope() as session:
        out = await c.analysis_service.analyze("BTCUSDT", session=session, persist=False, use_llm=False)
        r = out.result
        mc = r.market_context or {}
        print("analyze", r.direction.value, "score", round(r.score,2),
              "coin", None if r.coin_score is None else round(r.coin_score,2),
              "blend", bool(mc.get("blend")),
              "intel", bool(mc.get("intelligence")),
              "expl", bool(mc.get("explainability")))
        rows = (await session.execute(text("""
            SELECT COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE market_context ? 'blend') AS with_blend,
                   COUNT(*) FILTER (WHERE market_context ? 'intelligence') AS with_intel
            FROM signals WHERE created_at >= NOW() - INTERVAL '24 hours'
        """))).mappings().one()
        print("signals_24h", dict(rows))
    await c.aclose()
asyncio.run(main())
PY

echo "==> DB AUDIT BEFORE"
bash scripts/_vps_db_audit_universe.sh || true

echo "==> DATA PRUNE (Top-N universe + candle retention)"
docker compose exec -T worker python -m app.cli data prune

echo "==> DB AUDIT AFTER"
bash scripts/_vps_db_audit_universe.sh || true

echo "==> DASHBOARD (ensure latest)"
bash scripts/_vps_deploy_dashboard_only.sh || {
  # fallback inline if script not on disk yet
  WEB_ROOT=/var/www/alpha-desk
  BRANCH=origin/cursor/trading-dashboard-efe9
  rm -rf /tmp/alpha-desk-src && mkdir -p /tmp/alpha-desk-src
  git archive "$BRANCH" trading-dashboard | tar -x -C /tmp/alpha-desk-src
  cd /tmp/alpha-desk-src/trading-dashboard && npm ci && npm run build
  rm -rf "${WEB_ROOT:?}/"* && cp -a dist/. "$WEB_ROOT/" && chown -R www-data:www-data "$WEB_ROOT"
  nginx -t && systemctl reload nginx
  cd /opt/alpha-trade-oracle-bot
}

echo "==> FINAL DESK"
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk.json"))
p=d["portfolio"]; mr=d.get("marketRegime") or {}
print("desk", p.get("equity"), p.get("realizedPnl"), "closed", p.get("closedTrades"),
      "regime", mr.get("biasLabel"))
PY
docker compose ps --format 'table {{.Name}}\t{{.Status}}'
echo "===== FINISH ALL DONE ====="
