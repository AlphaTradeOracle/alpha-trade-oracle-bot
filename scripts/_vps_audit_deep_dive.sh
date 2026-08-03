#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot

python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path('/tmp/adversarial_full_audit.out').read_text())
for w in d.get('warnings') or []:
  if w.get('code')=='TP_GEOMETRY':
    print('TP_SAMPLE')
    print(json.dumps(w.get('sample'), indent=2)[:2500])
sf=d.get('signal_funnel') or {}
print('NEAR_MISS', json.dumps(sf.get('short_near_miss_bins')[:12], indent=2))
print('HOURLY_TAIL', json.dumps((sf.get('paper_gate_hourly_48h') or [])[-10:], indent=2))
print('JOBS', json.dumps(d.get('scan_jobs'), indent=2, default=str)[:3000])
PY

docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
\echo === ASSET COLUMNS ===
SELECT column_name FROM information_schema.columns WHERE table_name='assets' ORDER BY 1;

\echo === UNIVERSE BREAKDOWN ===
SELECT count(*) AS n FROM assets;
SELECT count(*) FILTER (WHERE in_universe) AS in_universe,
       count(*) FILTER (WHERE is_active) AS is_active
FROM assets;

\echo === LONG SCORES SINCE RESET ===
SELECT direction, count(*), round(min(score)::numeric,1) AS min_s, round(max(score)::numeric,1) AS max_s
FROM signals
WHERE created_at >= '2026-07-31T16:32:35+00:00'
  AND direction IN ('LONG','STRONG_LONG')
GROUP BY 1;

SELECT width_bucket(score, 50, 100, 10) AS bucket, count(*),
       round(min(score)::numeric,1), round(max(score)::numeric,1)
FROM signals
WHERE created_at >= '2026-07-31T16:32:35+00:00'
  AND direction IN ('LONG','STRONG_LONG')
GROUP BY 1 ORDER BY 1;

\echo === PAPER GATE LONGS (score>=75) ===
SELECT count(*) FROM signals
WHERE created_at >= '2026-07-31T16:32:35+00:00'
  AND direction IN ('LONG','STRONG_LONG') AND score >= 75;

\echo === CLOSE REASONS ===
SELECT COALESCE(close_reason,'(null)') AS reason, count(*)
FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
  AND status='closed'
GROUP BY 1 ORDER BY 2 DESC;

\echo === ACCOUNT VS SUM CLOSED ===
SELECT round(a.cash_balance::numeric,2) cash,
       round(a.realized_pnl::numeric,2) realized,
       round(sum(p.realized_pnl)::numeric,2) sum_closed
FROM paper_accounts a
LEFT JOIN paper_positions p ON p.account_id=a.id AND p.status='closed'
WHERE a.name='default'
GROUP BY a.id;

\echo === SCHEDULED JOBS ===
SELECT job_key, last_status, run_count, last_success_at, next_run_at, is_enabled,
       left(COALESCE(last_error,''), 80) AS err
FROM scheduled_jobs ORDER BY job_key;
SQL

# Desk math via app network
echo "=== DESK_MATH ==="
docker compose run --rm --no-deps \
  -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
  -v /tmp:/tmp \
  worker python /app/scripts/vps_audit_desk_values.py --out /tmp/desk_math_audit.json \
  >/tmp/audit_desk_math.out 2>/tmp/audit_desk_math.err || true
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/tmp/desk_math_audit.json')
if p.exists():
    d=json.loads(p.read_text())
    print('final_ok', d.get('final_ok'))
    print('issues', d.get('issues'))
    print('warnings', d.get('warnings'))
    print('trade_ok', d.get('trade_ok'), 'trade_fail', d.get('trade_fail'))
    print('portfolio', d.get('portfolio'))
else:
    print('missing desk math')
    print(Path('/tmp/audit_desk_math.err').read_text(errors='replace')[-2000:])
PY

# Local API from host (not container)
echo "=== HOST DESK API ==="
curl -fsS --max-time 20 http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_snap_host.json
python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path('/tmp/desk_snap_host.json').read_text())
p=d.get('portfolio') or {}
print({k:p.get(k) for k in ['equity','cash','realizedPnl','accountRealizedPnl','closedTrades','openPositions','pendingOrders','winRatePct','totalReturnPct','profitFactor']})
# vs public
import urllib.request
pub=json.loads(urllib.request.urlopen('https://alpha-trade-oracle.com/api/v1/desk/snapshot', timeout=20).read())
pp=pub.get('portfolio') or {}
diffs=[]
for k in p:
    if k in pp and p[k]!=pp[k]:
        try:
            if abs(float(p[k])-float(pp[k]))<0.05: continue
        except Exception:
            pass
        diffs.append((k,p[k],pp[k]))
print('host_vs_public_diffs', diffs)
PY
