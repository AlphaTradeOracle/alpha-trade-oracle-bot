#!/usr/bin/env bash
# Option A: roll VPS to pre-trendline commit 10d5898, disable TL env, rebuild paper (no allowlist).
set -eu
cd /opt/alpha-trade-oracle-bot

SINCE="${SINCE:-2026-07-31T16:32:35+00:00}"
TARGET="${TARGET:-10d5898}"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) ROLLBACK A → $TARGET ====="

echo "=== stop sims ==="
pkill -9 -f 'run_top400_paper_parity' 2>/dev/null || true
pkill -9 -f '_vps_run_global' 2>/dev/null || true
docker ps -q --filter name=worker-run | xargs -r docker rm -f || true

echo "=== git reset ==="
git fetch origin main
git reset --hard "$TARGET"
echo "HEAD=$(git rev-parse --short HEAD) $(git log -1 --format=%s)"

echo "=== disable trendline env ==="
# Comment out or set false; keep keys for later re-enable.
if grep -q '^SIGNAL_TRENDLINE_GATE_ENABLED=' .env; then
  sed -i 's/^SIGNAL_TRENDLINE_GATE_ENABLED=.*/SIGNAL_TRENDLINE_GATE_ENABLED=false/' .env
else
  echo 'SIGNAL_TRENDLINE_GATE_ENABLED=false' >> .env
fi
grep -E '^SIGNAL_TRENDLINE_' .env || true

echo "=== docker build worker+app ==="
docker compose build worker app
docker compose up -d worker app
sleep 12
docker compose ps

echo "=== verify no trendline module required ==="
docker compose exec -T worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
print("has_tl", hasattr(s, "signal_trendline_gate_enabled"))
if hasattr(s, "signal_trendline_gate_enabled"):
    print("tl_enabled", s.signal_trendline_gate_enabled)
print("retest", s.paper_retest_entry_enabled)
print("short_max", s.signal_short_max_score)
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) START PAPER REBUILD (no symbols-file) ====="
# Full stream since reset — no allowlist. Long-running.
nohup docker compose run --rm --no-deps \
  -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
  worker python -m app.cli paper rebuild \
    --since "$SINCE" \
    --all-signals \
  > /tmp/paper_rebuild_rollback_a.log 2>&1 &
echo "REBUILD_PID=$!"
sleep 3
tail -20 /tmp/paper_rebuild_rollback_a.log || true
echo "===== DEPLOY DONE — rebuild running in background ====="
echo "Monitor: tail -f /tmp/paper_rebuild_rollback_a.log"
