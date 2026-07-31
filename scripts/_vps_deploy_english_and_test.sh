#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

git fetch origin main
git reset --hard origin/main
echo "HEAD=$(git rev-parse --short HEAD)"

ids=$(docker ps -aq --filter name=alpha-trade-oracle-worker || true)
if [[ -n "${ids}" ]]; then
  docker rm -f ${ids} || true
fi

docker compose up -d --build --force-recreate worker
docker compose ps worker

cp /tmp/_vps_send_chart_tests.sh scripts/_vps_send_chart_tests.sh
chmod +x scripts/_vps_send_chart_tests.sh
bash scripts/_vps_send_chart_tests.sh
