#!/usr/bin/env python3
"""Minimal SPA-aware static server for the built prototype.

Serves ./dist and falls back to index.html for client-side routes, so
deep links like /analytics work when sharing a preview build.

Usage: python3 scripts/serve-static.py [port]
"""

from __future__ import annotations

import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist")


class SpaHandler(SimpleHTTPRequestHandler):
    def send_head(self):  # noqa: D102 - stdlib override
        path = self.translate_path(self.path)
        is_asset = os.path.isfile(path)
        # Unknown non-file routes belong to the client-side router.
        if not is_asset and "." not in os.path.basename(path):
            self.path = "/index.html"
        return super().send_head()

    def end_headers(self):  # noqa: D102 - stdlib override
        # Prototype builds change often; never let a proxy or browser pin them.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, *args):  # noqa: D102 - quieter output
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
    handler = partial(SpaHandler, directory=os.path.normpath(DIST))
    with ThreadingHTTPServer(("0.0.0.0", port), handler) as httpd:
        print(f"Serving dist on http://0.0.0.0:{port} (SPA fallback enabled)")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
