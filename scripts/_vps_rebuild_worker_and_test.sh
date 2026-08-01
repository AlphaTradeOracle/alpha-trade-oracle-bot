#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

git fetch origin main
git reset --hard origin/main
echo "HEAD=$(git rev-parse --short HEAD)"

# Wipe recreate leftovers
ids=$(docker ps -aq --filter name=alpha-trade-oracle || true)
if [[ -n "${ids}" ]]; then
  docker rm -f ${ids} || true
fi

# Force no-cache rebuild so pip install picks up formatting.py
docker compose build --no-cache worker
docker compose up -d postgres redis
docker compose up -d migrate
docker compose up -d worker
docker compose ps worker

docker compose cp scripts/_vps_verify_brand.py worker:/tmp/_vps_verify_brand.py
docker compose exec -T worker python /tmp/_vps_verify_brand.py

bash scripts/_vps_send_chart_tests.sh
