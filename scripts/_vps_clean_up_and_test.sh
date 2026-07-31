#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

git fetch origin main
git reset --hard origin/main
echo "HEAD=$(git rev-parse --short HEAD)"

# Clear recreate leftovers for worker + migrate
ids=$(docker ps -aq --filter name=alpha-trade-oracle || true)
if [[ -n "${ids}" ]]; then
  docker rm -f ${ids} || true
fi

docker compose up -d --build postgres redis
docker compose up -d --build migrate
docker compose up -d --build worker
docker compose ps

cp /tmp/_vps_send_chart_tests.sh scripts/_vps_send_chart_tests.sh
chmod +x scripts/_vps_send_chart_tests.sh
bash scripts/_vps_send_chart_tests.sh
