#!/usr/bin/env bash
set -euo pipefail
echo "===== DISK BEFORE ====="
df -h / /var/lib/docker 2>/dev/null || df -h /
docker system df || true

echo "==> prune docker unused"
docker container prune -f || true
docker image prune -af || true
docker builder prune -af || true
docker volume prune -f || true
# keep named volumes (postgres/redis data)

echo "==> clear temp build caches"
rm -rf /tmp/alpha-desk-src /tmp/npm-* /tmp/v8-* 2>/dev/null || true
rm -rf /root/.npm/_cacache 2>/dev/null || true
journalctl --vacuum-size=80M || true
# old docker logs if huge
find /var/lib/docker/containers -name '*-json.log' -size +50M -exec truncate -s 0 {} \; 2>/dev/null || true

echo "===== DISK AFTER ====="
df -h /
docker system df || true
echo DONE
