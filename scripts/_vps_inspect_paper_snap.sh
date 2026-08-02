#!/bin/bash
set -euo pipefail
ls -la /tmp/paper_snap.* 2>/dev/null || true
echo "---JSON head---"
head -c 2500 /tmp/paper_snap.json 2>/dev/null || true
echo
echo "---SQL head---"
head -50 /tmp/paper_snap.sql 2>/dev/null || true
echo "---JSON meta---"
python3 <<'PY'
import json
from pathlib import Path
p = Path("/tmp/paper_snap.json")
if not p.exists():
    print("no json")
    raise SystemExit(0)
d = json.loads(p.read_text())
print("type", type(d).__name__)
if isinstance(d, dict):
    print("keys", list(d.keys())[:40])
    for k, v in d.items():
        if isinstance(v, list):
            print(f"  {k}: list len={len(v)}")
        elif isinstance(v, dict):
            print(f"  {k}: dict keys={list(v.keys())[:20]}")
        else:
            print(f"  {k}: {v!r}"[:200])
elif isinstance(d, list):
    print("list len", len(d))
    if d:
        print("first keys", list(d[0].keys()) if isinstance(d[0], dict) else type(d[0]))
PY
echo "---SQL row counts---"
grep -E "COPY public.paper_|INSERT INTO public.paper_" /tmp/paper_snap.sql | head -20 || true
wc -l /tmp/paper_snap.sql
