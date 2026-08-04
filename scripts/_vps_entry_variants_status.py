"""Print entry-variants sim status from inside the worker container."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

prefix = sys.argv[1] if len(sys.argv) > 1 else "entry_variants_top100_7d"
LOG = Path(f"/tmp/{prefix}.log")
OUT = Path(f"/tmp/{prefix}.json")
PART = Path(f"/tmp/{prefix}.partial.json")

found = []
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode()
    except Exception:
        continue
    if "run_entry_variants_top400_7d" in cmd:
        found.append(pid)
print("PROCS", found or "NONE")
for p in (LOG, OUT, PART):
    if p.exists():
        print(f"{p.name} size={p.stat().st_size}")
    else:
        print(f"{p.name} missing")
if LOG.exists():
    text = LOG.read_text(encoding="utf-8", errors="replace")
    print("---LOG_TAIL---")
    print(text[-2500:])
if PART.exists():
    try:
        data = json.loads(PART.read_text(encoding="utf-8"))
        print(
            "PARTIAL",
            f"{data.get('done_symbols')}/{data.get('total_symbols')}",
            "partial=" + str(bool(data.get("partial"))),
        )
        for row in data.get("ranking") or []:
            print(
                " ",
                row.get("key"),
                "end=",
                row.get("end_equity"),
                "net=",
                row.get("net_pnl"),
                "closed=",
                row.get("closed"),
                "WR=",
                row.get("win_rate"),
            )
    except Exception as exc:
        print("PARTIAL_PARSE_ERR", exc)
if OUT.exists():
    try:
        data = json.loads(OUT.read_text(encoding="utf-8"))
        print("FINAL runtime_s=", data.get("runtime_seconds"))
        for row in data.get("ranking") or []:
            print(row)
    except Exception as exc:
        print("OUT_PARSE_ERR", exc)
