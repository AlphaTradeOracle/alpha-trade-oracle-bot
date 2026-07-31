#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "HEAD=$(git rev-parse --short HEAD)"

# Clear stale recreate leftovers that block the compose name.
ids=$(docker ps -aq --filter name=alpha-trade-oracle-worker || true)
if [[ -n "${ids}" ]]; then
  docker rm -f ${ids} || true
fi

docker compose up -d worker
docker compose ps worker

cp /tmp/_vps_send_chart_tests.sh scripts/_vps_send_chart_tests.sh
chmod +x scripts/_vps_send_chart_tests.sh
bash scripts/_vps_send_chart_tests.sh
