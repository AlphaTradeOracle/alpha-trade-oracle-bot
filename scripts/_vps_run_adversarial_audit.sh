#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) ADVERSARIAL AUDIT ====="
echo "HEAD=$(git rev-parse --short HEAD)"

# Ensure script available in container via bind
cp -f /tmp/_vps_adversarial_full_audit.py scripts/_vps_adversarial_full_audit.py 2>/dev/null || true

git fetch origin main >/dev/null 2>&1 || true
export AUDIT_GIT_HEAD="$(git rev-parse --short HEAD)"
export AUDIT_GIT_ORIGIN="$(git rev-parse --short origin/main 2>/dev/null || echo '?')"
export AUDIT_GIT_BEHIND="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"
echo "GIT head=$AUDIT_GIT_HEAD origin=$AUDIT_GIT_ORIGIN behind=$AUDIT_GIT_BEHIND"

docker compose run --rm --no-deps \
  -e AUDIT_GIT_HEAD -e AUDIT_GIT_ORIGIN -e AUDIT_GIT_BEHIND \
  -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
  -v /tmp:/tmp \
  worker python /app/scripts/_vps_adversarial_full_audit.py \
  >/tmp/adversarial_full_audit.out 2>/tmp/adversarial_full_audit.err

echo "EXIT=$?"
# print summary
python3 - <<'PY'
import json
from pathlib import Path
raw = Path("/tmp/adversarial_full_audit.out").read_text(encoding="utf-8", errors="replace")
# find JSON
start = raw.find("{")
if start < 0:
    print("NO_JSON")
    print(raw[-3000:])
    print("---ERR---")
    print(Path("/tmp/adversarial_full_audit.err").read_text(encoding="utf-8", errors="replace")[-3000:])
    raise SystemExit(1)
d = json.loads(raw[start:])
Path("/tmp/adversarial_full_audit.json").write_text(json.dumps(d, indent=2), encoding="utf-8")
print("FINAL_OK", d.get("final_ok"))
print("FAILS", d.get("fail_count"), "WARNS", d.get("warn_count"), "OKS", d.get("ok_count"))
print("HEAD", d.get("head"), "behind", d.get("behind_origin"))
print("BOOK", json.dumps(d.get("book")))
print("ACCOUNT", json.dumps(d.get("account")))
print("FUNNEL", json.dumps({k:v for k,v in (d.get("signal_funnel") or {}).items() if k not in ("paper_gate_hourly_48h","short_near_miss_bins")}))
print("FINDINGS:")
for f in d.get("findings") or []:
    print(" -", f.get("code"), f.get("msg"))
print("WARNINGS:")
for w in d.get("warnings") or []:
    print(" -", w.get("code"), w.get("msg")[:160])
print("OK_SAMPLE:", (d.get("ok_checks") or [])[:20])
PY

# Also run desk_math if present
if [[ -f scripts/vps_audit_desk_values.py ]]; then
  echo ""
  echo "=== DESK_MATH_SCRIPT ==="
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
    d = json.loads(p.read_text())
    print("DESK_MATH final_ok=", d.get("final_ok"), "issues=", d.get("issues"), "trade_fail=", d.get("trade_fail"))
else:
    print("DESK_MATH missing")
    print(Path("/tmp/audit_desk_math.err").read_text(errors="replace")[-1500:])
PY
fi

echo "===== AUDIT PACK DONE ====="
