#!/usr/bin/env bash
# Full 100% audit pack: paper / signals / exits / desk / site
set -eu
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) HUNDRED AUDIT START ====="
echo "HEAD=$(git rev-parse --short HEAD)"
git fetch origin main cursor/trading-dashboard-efe9 >/dev/null 2>&1 || true
echo "origin_main=$(git rev-parse --short origin/main 2>/dev/null || echo '?')"
echo "dash=$(git rev-parse --short origin/cursor/trading-dashboard-efe9 2>/dev/null || echo '?')"

run_worker() {
  local name="$1"; shift
  echo ""
  echo "=== $name ==="
  docker compose run --rm --no-deps \
    -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
    worker "$@" 2>/tmp/audit_${name}.err | tee "/tmp/audit_${name}.out" | tail -n 5
  local ec=${PIPESTATUS[0]}
  echo "EXIT_$name=$ec"
  if [[ -s /tmp/audit_${name}.err ]]; then
    echo "ERR_TAIL_$name:"
    tail -n 15 "/tmp/audit_${name}.err" || true
  fi
}

# 1) Paper since-reset verify
run_worker paper_verify python /app/scripts/vps_verify_paper_since_reset.py
python3 - <<'PY'
import json
from pathlib import Path
raw = Path("/tmp/audit_paper_verify.out").read_text(encoding="utf-8", errors="replace")
start = raw.rfind("{")
d = json.loads(raw[start:]) if start >= 0 else {}
v = d.get("verdict") or {}
acct = d.get("account") or {}
book = d.get("db_book") or {}
print("PAPER_VERIFY", json.dumps({
    "FINAL_OK": v.get("FINAL_OK"),
    "identity_ok": v.get("account_identity_ok") or acct.get("identity_ok"),
    "expected_fills": v.get("expected_fills"),
    "db_closed": v.get("db_closed_allowlist") or book.get("closed"),
    "missing": v.get("missing_fills"),
    "extra": v.get("extra_fills"),
    "geom": v.get("geometry_mismatches"),
    "should_have": v.get("should_have_traded"),
    "cash": acct.get("cash"),
    "realized": acct.get("realized"),
}, indent=2))
Path("/tmp/audit_paper_verify.json").write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")
PY

# 2) Desk math
run_worker desk_math python /app/scripts/vps_audit_desk_values.py --out /tmp/desk_math_audit.json
# copy out of container volume if written inside - script writes to host via bind? /tmp is container
# re-run with out under /app/scripts tmp
docker compose run --rm --no-deps \
  -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
  -v /tmp:/tmp \
  worker python /app/scripts/vps_audit_desk_values.py --out /tmp/desk_math_audit.json \
  >/tmp/audit_desk_math.out 2>/tmp/audit_desk_math.err || true
python3 - <<'PY'
import json
from pathlib import Path
p = Path("/tmp/desk_math_audit.json")
if p.exists():
    d = json.loads(p.read_text(encoding="utf-8"))
    print("DESK_MATH", json.dumps({
        "final_ok": d.get("final_ok"),
        "issues": d.get("issues"),
        "warnings": d.get("warnings"),
        "trade_ok": d.get("trade_ok"),
        "trade_fail": d.get("trade_fail"),
        "pending_n": d.get("pending_n"),
        "open_n": d.get("open_n"),
        "closed_n": d.get("closed_n"),
        "portfolio": d.get("portfolio"),
    }, indent=2, default=str))
else:
    print("DESK_MATH_MISSING")
    print(Path("/tmp/audit_desk_math.out").read_text(encoding="utf-8", errors="replace")[-2000:])
PY

# 3) Perp exit geometry replay
if [[ -f scripts/vps_verify_perp_exits.py ]]; then
  run_worker perp_exits python /app/scripts/vps_verify_perp_exits.py
fi

# 4) Full system audit (if present)
if [[ -f scripts/vps_full_system_audit.py ]]; then
  run_worker full_system python /app/scripts/vps_full_system_audit.py
fi

# 5) Missed signals (allowlist since reset)
if [[ -f scripts/vps_missed_signals_audit.py ]]; then
  run_worker missed python /app/scripts/vps_missed_signals_audit.py
fi

# 6) Desk API + public site
echo ""
echo "=== DESK_API ==="
curl -fsS --max-time 20 "http://127.0.0.1:8000/api/v1/desk/snapshot" -o /tmp/desk_snap.json
curl -fsS --max-time 15 "http://127.0.0.1:8000/api/v1/desk/top-coins?limit=10" -o /tmp/top_coins.json
curl -fsS -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:8000/health || echo "health=FAIL"
python3 - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("/tmp/desk_snap.json").read_text(encoding="utf-8"))
p = d.get("portfolio") or {}
trades = d.get("trades") or d.get("recentTrades") or []
# normalize trade list
if isinstance(trades, dict):
    trades = trades.get("items") or trades.get("trades") or []
opens = [t for t in trades if str(t.get("status","")).lower() in ("open","pending")]
closed = [t for t in trades if str(t.get("status","")).lower() == "closed"]
pending = [t for t in trades if str(t.get("status","")).lower() == "pending"]
print("DESK_SNAP", json.dumps({
    "equity": p.get("equity"),
    "cash": p.get("cash"),
    "realizedPnl": p.get("realizedPnl") or p.get("accountRealizedPnl"),
    "openPositions": p.get("openPositions"),
    "pendingOrders": p.get("pendingOrders"),
    "closedTrades": p.get("closedTrades"),
    "winRatePct": p.get("winRatePct"),
    "totalReturnPct": p.get("totalReturnPct"),
    "trade_list_n": len(trades),
    "list_open": len(opens),
    "list_pending": len(pending),
    "list_closed_sample": len(closed),
    "has_regime": (d.get("marketRegime") or d.get("market_regime")) is not None,
}, indent=2))
# pending zone sanity
bad_zones = []
for t in trades:
    if str(t.get("status","")).lower() != "pending":
        continue
    lo, hi = t.get("entryZoneLow"), t.get("entryZoneHigh")
    if lo is None or hi is None:
        continue
    if 0 < float(lo) < 2 and 0 < float(hi) <= 2:
        # could be low-priced coin OR fake ATR — flag if both < 2 and stop >> hi
        stop = float(t.get("stop") or t.get("stopLoss") or 0)
        if stop > 2 and float(hi) <= 2:
            bad_zones.append({"symbol": t.get("symbol"), "lo": lo, "hi": hi, "stop": stop})
print("PENDING_FAKE_ATR_ZONES", json.dumps(bad_zones, indent=2))
Path("/tmp/desk_snap_summary.json").write_text(json.dumps({
    "portfolio": p,
    "bad_zones": bad_zones,
}, indent=2), encoding="utf-8")
PY

echo ""
echo "=== PUBLIC_SITE ==="
for url in \
  "https://alpha-trade-oracle.com/" \
  "https://alpha-trade-oracle.com/api/v1/desk/snapshot" \
  "https://alpha-trade-oracle.com/api/v1/desk/top-coins?limit=5" \
  "https://alpha-trade-oracle.com/health"
do
  code=$(curl -fsS -o /tmp/site_body.bin -w "%{http_code}" --max-time 20 "$url" || echo FAIL)
  bytes=$(wc -c </tmp/site_body.bin 2>/dev/null || echo 0)
  echo "$code  bytes=$bytes  $url"
done

# Cross-check public snapshot equity vs local
python3 - <<'PY'
import json, urllib.request
from pathlib import Path
local = json.loads(Path("/tmp/desk_snap.json").read_text())
try:
    with urllib.request.urlopen("https://alpha-trade-oracle.com/api/v1/desk/snapshot", timeout=20) as r:
        pub = json.loads(r.read().decode())
except Exception as e:
    print("PUBLIC_SNAP_FAIL", e)
    raise SystemExit(0)
lp = local.get("portfolio") or {}
pp = pub.get("portfolio") or {}
keys = ["equity","cash","realizedPnl","accountRealizedPnl","openPositions","pendingOrders","closedTrades","winRatePct"]
diff = []
for k in keys:
    a, b = lp.get(k), pp.get(k)
    if a != b and (a is not None or b is not None):
        # float tolerance
        try:
            if abs(float(a)-float(b)) < 0.02:
                continue
        except Exception:
            pass
        diff.append({"key": k, "local": a, "public": b})
print("PUBLIC_VS_LOCAL", json.dumps({"diffs": diff, "local_equity": lp.get("equity"), "public_equity": pp.get("equity")}, indent=2))
PY

# DB quick stats
echo ""
echo "=== DB_STATS ==="
set -a; . ./.env; set +a
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -A <<'SQL'
SELECT 'paper|'||status||'|'||COUNT(*)||'|'||COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions WHERE account_id=1 GROUP BY status ORDER BY status;
SELECT 'acct|'||ROUND(cash_balance::numeric,2)||'|'||ROUND(realized_pnl::numeric,2)||'|'||ROUND(initial_balance::numeric,2)
FROM paper_accounts WHERE id=1;
SELECT 'signals_24h|'||COUNT(*) FROM signals WHERE created_at > NOW() - INTERVAL '24 hours';
SELECT 'tradeable_24h|'||COUNT(*) FROM signals
WHERE created_at > NOW() - INTERVAL '24 hours'
  AND direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT');
SELECT 'dispatched_24h|'||COUNT(*) FROM signals
WHERE created_at > NOW() - INTERVAL '24 hours' AND is_dispatched=true;
SELECT 'universe|'||COUNT(*) FILTER (WHERE in_universe AND is_active) FROM assets;
SQL

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) HUNDRED AUDIT DONE ====="
