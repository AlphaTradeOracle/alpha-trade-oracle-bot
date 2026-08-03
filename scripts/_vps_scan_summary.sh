#!/usr/bin/env bash
set -euo pipefail
date -u
echo '--- scan outcome lines ---'
docker compose -f /opt/alpha-trade-oracle-bot/docker-compose.yml logs worker --since 15m 2>&1 \
  | grep -E 'scan_completed|deferred_until|paper_position_pending|paper_skip|signal_telegram|job_started|job_completed|cooldown' \
  | tail -50
echo '--- suppress reason counts this scan ---'
docker compose -f /opt/alpha-trade-oracle-bot/docker-compose.yml logs worker --since 15m 2>&1 \
  | grep '"event": "signal_suppressed"' \
  | sed -n 's/.*"reason": "\([^"]*\)".*/\1/p' \
  | sort | uniq -c | sort -rn | head -20
echo '--- detail snippets for cooldown / short band ---'
docker compose -f /opt/alpha-trade-oracle-bot/docker-compose.yml logs worker --since 15m 2>&1 \
  | grep -E 'Cooldown|maximum 30|SHORT|deferred|paper_skip|paper_position' \
  | tail -30
