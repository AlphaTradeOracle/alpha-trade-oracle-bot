"""Export rich partial ranking + equity_daily from running sim checkpoint."""
from __future__ import annotations

import json
import sys
from pathlib import Path

src = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/entry_variants_top100_7d.partial.json")
data = json.loads(src.read_text(encoding="utf-8"))
# Prefer full variants if present; else ranking only
out = {
    "partial": True,
    "done_symbols": data.get("done_symbols"),
    "total_symbols": data.get("total_symbols"),
    "ranking": data.get("ranking") or [],
    "variants": data.get("variants") or [],
}
print(json.dumps(out, indent=2))
