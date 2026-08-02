#!/bin/bash
set -euo pipefail
echo "=== paper_since_reset ==="
cat /tmp/paper_since_reset.txt 2>/dev/null || true
echo
echo "=== finish_paper_rebuild tail ==="
tail -80 /tmp/finish_paper_rebuild.log 2>/dev/null || true
echo
echo "=== paper_reset_symbols ==="
cat /tmp/paper_reset_symbols.txt 2>/dev/null || true
echo
echo "=== docker logs paper_position_closed (recent sample) ==="
cd /opt/alpha-trade-oracle-bot
# Look for any SQL dumps after Jul 31
find /opt /tmp /root -name '*.sql' -mtime -5 2>/dev/null | head -40
echo "=== redis desk keys ==="
docker compose exec -T redis redis-cli KEYS '*paper*' 2>/dev/null | head -20 || true
docker compose exec -T redis redis-cli KEYS '*desk*' 2>/dev/null | head -20 || true
