#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
LOG=/tmp/paper_rebuild_jul31.log
echo "start $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee "$LOG"
docker compose exec -T worker python -m app.cli paper rebuild \
  --since "2026-07-31T16:32:35+00:00" \
  --all-signals \
  --all-qualifying \
  >>"$LOG" 2>&1
echo "exit=$?" | tee -a "$LOG"
tail -n 40 "$LOG"
