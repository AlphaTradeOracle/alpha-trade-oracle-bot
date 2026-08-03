#!/usr/bin/env bash
# Quick 24/7 readiness snapshot.
set -eu
cd /opt/alpha-trade-oracle-bot
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) readiness ====="
echo "--- compose ---"
docker compose ps
echo "--- health ---"
curl -fsS http://127.0.0.1:8000/api/v1/health || echo HEALTH_FAIL
echo
echo "--- desk ---"
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import sys,json
d=json.load(sys.stdin)
p=d.get("portfolio") or {}
print({k:p.get(k) for k in ("equity","cashBalance","closedTrades","openPositions","pendingOrders","winRatePct","profitFactor","totalReturnPct")})
'
echo "--- env gates ---"
grep -E '^(ENABLE_PAPER|ENABLE_UNIVERSE|SIGNAL_|PAPER_|MARKET_REGIME_|TELEGRAM_SIGNAL|UNIVERSE_)' .env | sed 's/=.*/=***/' >/dev/null
grep -E '^(ENABLE_PAPER_TRADING|ENABLE_UNIVERSE_SCAN|SIGNAL_SHORT_|SIGNAL_MIN_SCORE|PAPER_RETEST|MARKET_REGIME_|TELEGRAM_SIGNAL_DISPATCH|UNIVERSE_TARGET|UNIVERSE_REQUIRE)' .env || true
echo "--- worker recent ---"
docker compose logs worker --since 45m 2>&1 | grep -E 'scheduler_started|job_started|job_completed|scan_completed|ERROR|Traceback|paper_' | tail -40
echo "--- error count 6h ---"
docker compose logs worker --since 6h 2>&1 | grep -c '"level": "error"' || true
docker compose logs worker --since 6h 2>&1 | grep -E '"level": "error"|Traceback' | tail -15
echo "--- redis cooldowns ---"
docker compose exec -T redis redis-cli --scan --pattern 'signal:cooldown:*' | wc -l
echo "--- disk ---"
df -h / /var/lib/docker 2>/dev/null | head -10
echo "===== done ====="
