"""Inspect entry-variants sim processes inside container."""
from __future__ import annotations

import os
from pathlib import Path


def main() -> None:
    for pid in sorted(
        (p for p in os.listdir("/proc") if p.isdigit()),
        key=int,
    ):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode()
        except Exception:
            continue
        if "entry_variants" not in cmd and "multiprocessing" not in cmd:
            continue
        try:
            stat = open(f"/proc/{pid}/stat").read().split()
            state = stat[2]
            ticks = int(stat[13]) + int(stat[14])
            rss_mb = int(stat[23]) * 4096 // (1024 * 1024)
        except Exception:
            state, ticks, rss_mb = "?", -1, -1
        print(f"pid={pid} state={state} cpu_ticks={ticks} rss_mb={rss_mb}")
        print(f"  {cmd[:200]}")

    for name in (
        "entry_variants_top100_7d",
        "entry_variants_top400_7d",
    ):
        for suffix in (".log", ".json", ".partial.json"):
            p = Path(f"/tmp/{name}{suffix}")
            if p.exists():
                print(f"{p.name} size={p.stat().st_size}")
            else:
                print(f"{p.name} missing")
        log = Path(f"/tmp/{name}.log")
        if log.exists():
            text = log.read_text(encoding="utf-8", errors="replace")
            print(f"--- {name} tail ---")
            print(text[-1200:])


if __name__ == "__main__":
    main()
