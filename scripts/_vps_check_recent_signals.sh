#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "=== ENV SCAN / UNIVERSE ==="
grep -E '^(SCAN_INTERVAL|UNIVERSE_|SIGNAL_|ENABLE_UNIVERSE|ENABLE_SCHEDULER)' .env | sort || true

echo "=== JOBS (recent scan) ==="
docker compose exec -T postgres \
  psql -U "${POSTGRES_USER:-alpha_trade_oracle}" -d "${POSTGRES_DB:-alpha_trade_oracle}" -c \
  "SELECT job_key, success, started_at, finished_at, error
   FROM scheduled_jobs
   WHERE job_key ILIKE '%scan%' OR job_type ILIKE '%scan%'
   ORDER BY COALESCE(finished_at, started_at) DESC NULLS LAST
   LIMIT 12;" 2>/dev/null || \
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c "\dt" | head -40

python3 - <<'PY'
import os, subprocess, textwrap
# load .env for credentials
env={}
for line in open("/opt/alpha-trade-oracle-bot/.env"):
    line=line.strip()
    if not line or line.startswith("#") or "=" not in line: continue
    k,v=line.split("=",1); env[k]=v.strip().strip('"').strip("'")
user=env.get("POSTGRES_USER","alpha_trade_oracle")
db=env.get("POSTGRES_DB","alpha_trade_oracle")

sql = r'''
\echo === SIGNALS last 48h ===
SELECT direction, COUNT(*) AS n
FROM signals
WHERE created_at > NOW() - INTERVAL '48 hours'
GROUP BY 1 ORDER BY n DESC;

\echo === ACTIONABLE last 7d ===
SELECT created_at AT TIME ZONE 'UTC' AS created_utc,
       symbol, direction, ROUND(score::numeric,1) AS score,
       LEFT(COALESCE(reason,''), 80) AS reason
FROM signals
WHERE created_at > NOW() - INTERVAL '7 days'
  AND direction IN ('long','short','strong_long','strong_short')
ORDER BY created_at DESC
LIMIT 25;

\echo === NEAR MISSES last 48h (score near band / no_trade) ===
SELECT created_at AT TIME ZONE 'UTC' AS created_utc,
       symbol, direction, ROUND(score::numeric,1) AS score,
       LEFT(COALESCE(reason,''), 100) AS reason
FROM signals
WHERE created_at > NOW() - INTERVAL '48 hours'
  AND (
    direction ILIKE '%no_trade%'
    OR direction = 'neutral'
    OR (direction ILIKE '%short%' AND score BETWEEN 17 AND 27)
    OR (direction ILIKE '%long%' AND score BETWEEN 70 AND 78)
  )
ORDER BY created_at DESC
LIMIT 40;

\echo === SCAN EVENTS last 24h ===
SELECT created_at AT TIME ZONE 'UTC' AS created_utc, event_type, LEFT(message,120) AS msg
FROM events
WHERE created_at > NOW() - INTERVAL '24 hours'
  AND (event_type ILIKE '%scan%' OR message ILIKE '%scan%' OR message ILIKE '%universe%')
ORDER BY created_at DESC
LIMIT 20;

\echo === UNIVERSE SIZE ===
SELECT COUNT(*) FILTER (WHERE in_universe) AS in_universe,
       COUNT(*) AS assets_total
FROM assets;
'''
# try via docker
cmd=["docker","compose","exec","-T","postgres","psql","-U",user,"-d",db,"-v","ON_ERROR_STOP=0"]
p=subprocess.run(cmd, input=sql, text=True, cwd="/opt/alpha-trade-oracle-bot", capture_output=True)
print(p.stdout)
if p.returncode!=0:
    print(p.stderr[:2000])
PY
