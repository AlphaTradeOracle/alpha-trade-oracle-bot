#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
git fetch origin main
git reset --hard origin/main
sed -i 's/\r$//' scripts/_vps_vacuum_final.sh || true

echo "==> recreate postgres with shm_size=256mb"
docker compose up -d postgres
sleep 3
docker exec alpha-trade-oracle-postgres df -h /dev/shm
docker compose up -d app worker

bash scripts/_vps_vacuum_final.sh
echo ALL_GOOD
