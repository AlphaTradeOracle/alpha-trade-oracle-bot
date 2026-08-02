#!/usr/bin/env bash
set -euo pipefail
echo "===== DISK ====="
df -h /
df -h /var/lib/docker 2>/dev/null || true

echo "===== TOP DIRS / ====="
du -xh / --max-depth=1 2>/dev/null | sort -hr | head -20

echo "===== /var ====="
du -xh /var --max-depth=2 2>/dev/null | sort -hr | head -25

echo "===== /opt ====="
du -xh /opt --max-depth=3 2>/dev/null | sort -hr | head -25

echo "===== DOCKER ====="
docker system df -v 2>/dev/null | head -80 || true

echo "===== POSTGRES DB SIZES ====="
cd /opt/alpha-trade-oracle-bot
export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT relname, pg_size_pretty(pg_total_relation_size(c.oid)) AS total
   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relkind='r'
   ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 12;"
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size;"

echo "===== LARGE FILES >200M ====="
find /var /opt /tmp /root -xdev -type f -size +200M 2>/dev/null | head -40
du -xh /var/lib/docker 2>/dev/null | sort -hr | head -15 || true
