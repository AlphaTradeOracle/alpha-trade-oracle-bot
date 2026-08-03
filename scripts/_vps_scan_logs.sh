#!/usr/bin/env bash
set -euo pipefail
date -u
echo '--- worker events ---'
docker compose -f /opt/alpha-trade-oracle-bot/docker-compose.yml logs worker --since 4h 2>&1 \
  | grep -E 'job_started|job_completed|scan_completed|paper_position|signal_suppressed|paper_skip|deferred_until_retest|SKR|scheduler_' \
  | tail -80
echo '--- redis SKR ttl ---'
docker compose -f /opt/alpha-trade-oracle-bot/docker-compose.yml exec -T redis redis-cli TTL signal:cooldown:SKRUSDT:1h
echo '--- cooldown count ---'
docker compose -f /opt/alpha-trade-oracle-bot/docker-compose.yml exec -T redis redis-cli --scan --pattern 'signal:cooldown:*' | wc -l
