#!/usr/bin/env python3
"""Aktualisiert LLM-Einstellungen in .env (Key via Umgebungsvariable K=base64)."""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

ENV_PATH = Path(os.environ.get("ENV_PATH", "/opt/alpha-trade-oracle-bot/.env"))


def main() -> int:
    raw = os.environ.get("K")
    if not raw:
        print("K env var missing", file=sys.stderr)
        return 1
    key = base64.b64decode(raw).decode()
    updates = {
        "LLM_PROVIDER": os.environ.get("LLM_PROVIDER", "openrouter"),
        "LLM_MODEL": os.environ.get("LLM_MODEL", "openai/gpt-4o-mini"),
        "LLM_API_KEY": key,
        "LLM_BASE_URL": os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
        "ENABLE_LLM_ANALYSIS": "true",
    }
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if "=" in line:
            name = line.split("=", 1)[0]
            if name in updates:
                out.append(f"{name}={updates[name]}")
                seen.add(name)
                continue
        out.append(line)
    for name, value in updates.items():
        if name not in seen:
            out.append(f"{name}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("env_updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
