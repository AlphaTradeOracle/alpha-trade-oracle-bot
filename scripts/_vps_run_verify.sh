#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
docker compose run --rm --no-deps worker \
  python /app/scripts/vps_verify_paper_since_reset.py \
  > /tmp/paper_verify.json 2>/tmp/paper_verify.err
echo "EXIT:$?"
python3 <<'PY'
import json
from pathlib import Path
p = Path("/tmp/paper_verify.json")
raw = p.read_text(encoding="utf-8").strip()
# script may print logs before JSON — take last JSON object
start = raw.rfind("{")
if start < 0:
    print("NO_JSON")
    print(raw[-2000:])
    raise SystemExit(1)
d = json.loads(raw[start:])
print("FINAL_OK", d.get("FINAL_OK"))
print("summary", json.dumps(d.get("summary"), indent=2))
for key in (
    "missing_fills",
    "extra_fills",
    "geometry_mismatches",
    "should_have_traded",
    "pnl_mismatches",
):
    val = d.get(key) or []
    print(f"{key}_n", len(val))
    if val and len(val) <= 20:
        print(json.dumps(val, indent=2, default=str)[:4000])
    elif val:
        print(json.dumps(val[:5], indent=2, default=str)[:2000])
print("ERR_TAIL:")
err = Path("/tmp/paper_verify.err").read_text(encoding="utf-8", errors="replace")
print(err[-2500:])
PY
