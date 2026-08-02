#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
PW="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
docker compose cp /tmp/_vps_desk_trade_check.py worker:/tmp/_vps_desk_trade_check.py
docker compose exec -T -e POSTGRES_PASSWORD="$PW" worker python /tmp/_vps_desk_trade_check.py
