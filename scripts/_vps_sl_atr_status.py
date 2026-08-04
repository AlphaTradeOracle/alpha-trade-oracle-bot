from pathlib import Path
import os

for p in (
    Path("/tmp/sl_atr_variants.log"),
    Path("/tmp/sl_atr_variants.json"),
):
    print(p.name, "exists" if p.exists() else "missing", f"size={p.stat().st_size}" if p.exists() else "")

found = []
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode()
    except Exception:
        continue
    if "simulate_sl_atr" in cmd:
        found.append((pid, cmd[:160]))
print("PROCS", found or "NONE")
log = Path("/tmp/sl_atr_variants.log")
if log.exists():
    print("---LOG---")
    print(log.read_text(encoding="utf-8", errors="replace")[-3000:])
