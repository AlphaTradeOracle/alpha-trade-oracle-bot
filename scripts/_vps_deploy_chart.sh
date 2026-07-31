#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
git pull --ff-only origin main
docker compose up -d --build worker
docker compose cp /tmp/_send_ela_test_signal.py worker:/tmp/_send_ela_test_signal.py 2>/dev/null || true
docker compose exec -T worker python /tmp/_send_ela_test_signal.py
