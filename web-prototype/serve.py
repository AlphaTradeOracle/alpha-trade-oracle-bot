#!/usr/bin/env python3
"""Lokaler Dashboard-Server: Static files + Candle-Proxy + LLM-Eval (CORS).

Usage:
  python web-prototype/serve.py
  → http://127.0.0.1:8765/dashboard.html
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
PORT = 8765

ALLOWED_INTERVALS = frozenset({"15m", "1h", "4h", "1d"})
BINANCE_TF = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
KUCOIN_TF = {"15m": "15min", "1h": "1hour", "4h": "4hour", "1d": "1day"}

EVAL_MODEL = "openai/gpt-5.5"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
# Same primary var as app/core/config.py (Settings.llm_api_key → LLM_API_KEY),
# plus common aliases used with OpenRouter / OpenAI-compatible providers.
_LLM_KEY_VARS = ("LLM_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY")


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines; skip comments/blank; ignore empty values."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.lower().startswith("export "):
            key = key[7:].strip()
        val = val.strip().strip('"').strip("'")
        if key and val:
            out[key] = val
    return out


def _load_dotenv() -> None:
    """Load .env files the same way the bot expects (repo-root .env).

    Search order (later files do not override earlier non-empty env):
    1) already-set process env
    2) cwd/.env
    3) repo-root/.env  (pydantic Settings env_file=\".env\")
    4) web-prototype/.env
    Empty assignments never shadow a real key from another source.
    Falls back to python-dotenv if installed.
    """
    candidates = [
        Path.cwd() / ".env",
        REPO_ROOT / ".env",
        ROOT / ".env",
    ]
    seen: set[Path] = set()
    merged: dict[str, str] = {}
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        for key, val in _parse_dotenv(path).items():
            merged.setdefault(key, val)

    if not any(merged.get(k) for k in _LLM_KEY_VARS):
        try:
            from dotenv import load_dotenv  # type: ignore[import-untyped]
        except ImportError:
            pass
        else:
            for path in candidates:
                if path.is_file():
                    load_dotenv(path, override=False)

    for key, val in merged.items():
        existing = os.environ.get(key, "").strip()
        if not existing:
            os.environ[key] = val


_load_dotenv()


def _llm_api_key() -> str:
    for name in _LLM_KEY_VARS:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def _llm_key_source() -> str | None:
    """Which env var name provided the key (value never returned)."""
    for name in _LLM_KEY_VARS:
        if (os.environ.get(name) or "").strip():
            return name
    return None


def _llm_base_url() -> str:
    return (os.environ.get("LLM_BASE_URL") or OPENROUTER_BASE).rstrip("/")


def fetch_binance(symbol: str, interval: str = "1h", limit: int = 120) -> list[dict]:
    sym = symbol.upper().replace("-", "").replace("_", "")
    url = (
        "https://api.binance.com/api/v3/klines?"
        + urllib.parse.urlencode({"symbol": sym, "interval": interval, "limit": limit})
    )
    with urllib.request.urlopen(url, timeout=12) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    out = []
    for row in raw:
        out.append(
            {
                "time": int(row[0] // 1000),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
        )
    return out


def fetch_kucoin(symbol: str, candle_type: str = "1hour", limit: int = 120) -> list[dict]:
    # KuCoin uses BASE-QUOTE
    sym = symbol.upper().replace("_", "-")
    if "-" not in sym and sym.endswith("USDT"):
        sym = sym[:-4] + "-USDT"
    url = (
        "https://api.kucoin.com/api/v1/market/candles?"
        + urllib.parse.urlencode({"symbol": sym, "type": candle_type})
    )
    with urllib.request.urlopen(url, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows = payload.get("data") or []
    # KuCoin returns newest first: [time, open, close, high, low, volume, amount]
    candles = []
    for row in rows[:limit]:
        candles.append(
            {
                "time": int(row[0]),
                "open": float(row[1]),
                "close": float(row[2]),
                "high": float(row[3]),
                "low": float(row[4]),
                "volume": float(row[5]),
            }
        )
    candles.sort(key=lambda c: c["time"])
    return candles


def _normalize_timeframe(raw: str) -> str:
    tf = (raw or "1h").strip().lower()
    aliases = {
        "15": "15m",
        "15min": "15m",
        "60": "1h",
        "60m": "1h",
        "1hour": "1h",
        "240": "4h",
        "4hour": "4h",
        "1day": "1d",
        "d": "1d",
        "day": "1d",
    }
    tf = aliases.get(tf, tf)
    return tf if tf in ALLOWED_INTERVALS else "1h"


def call_openrouter_evaluate(payload: dict) -> dict:
    """Call GPT-5.5 via OpenRouter; return structured desk assessment."""
    api_key = _llm_api_key()
    if not api_key:
        return {
            "ok": False,
            "error": "missing_api_key",
            "message": (
                "Kein API-Key gefunden. Setze LLM_API_KEY (oder OPENROUTER_API_KEY / "
                "OPENAI_API_KEY) in der Projekt-.env bzw. Umgebung und starte "
                f"serve.py neu. Modell: {EVAL_MODEL} via OpenRouter."
            ),
        }

    system = (
        "Du bist ein knapper Trading-Desk-Analyst für Krypto-Futures/Spot. "
        "Antworte ausschließlich auf Deutsch und nur als JSON-Objekt (kein Markdown). "
        "Du bewertest ein bereits berechnetes Signal — erfinde keine Kurse. "
        "Schema: {"
        '"verdict":"keep|scale|exit|bullish|cautious|bearish",'
        '"confidence":0-100,'
        '"reasons":["...","...","..."],'
        '"risk_note":"...",'
        '"summary":"ein Satz"'
        "}. "
        "reasons: 3–5 kurze Bullet-Punkte. Experimental / testweise — keine Anlageberatung."
    )
    user = (
        "Bewerte dieses Signal (testweise LLM-Desk):\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    body = json.dumps(
        {
            "model": EVAL_MODEL,
            "temperature": 0.3,
            "max_tokens": 700,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{_llm_base_url()}/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:8765",
            "X-Title": "ATO Web Prototype LLM Eval",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        return {
            "ok": False,
            "error": "provider_http",
            "message": f"LLM-Anbieter HTTP {exc.code}: {detail}",
            "model": EVAL_MODEL,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": "provider_error",
            "message": str(exc),
            "model": EVAL_MODEL,
        }

    content = ""
    try:
        content = raw["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return {
            "ok": False,
            "error": "bad_response",
            "message": "Unerwartete LLM-Antwortstruktur",
            "model": EVAL_MODEL,
        }

    # Strip optional markdown fences
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "invalid_json",
            "message": "LLM lieferte kein gültiges JSON",
            "raw": content[:500],
            "model": EVAL_MODEL,
        }

    verdict = str(parsed.get("verdict") or "cautious").lower()
    reasons = parsed.get("reasons") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)]
    reasons = [str(r).strip() for r in reasons if str(r).strip()][:5]
    try:
        confidence = int(parsed.get("confidence", 50))
    except (TypeError, ValueError):
        confidence = 50
    confidence = max(0, min(100, confidence))

    return {
        "ok": True,
        "model": EVAL_MODEL,
        "experimental": True,
        "verdict": verdict,
        "confidence": confidence,
        "reasons": reasons,
        "risk_note": str(parsed.get("risk_note") or "").strip(),
        "summary": str(parsed.get("summary") or "").strip(),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_OPTIONS(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/candles":
            self._candles(parsed.query)
            return
        if parsed.path == "/api/llm/status":
            source = _llm_key_source()
            self._json_response(
                200,
                {
                    "configured": bool(source),
                    "key_var": source,
                    "model": EVAL_MODEL,
                    "base_url": _llm_base_url(),
                    "env_hint": (
                        "LLM_API_KEY (Bot-Standard) oder OPENROUTER_API_KEY / "
                        "OPENAI_API_KEY in Repo-.env setzen"
                    ),
                },
            )
            return
        if parsed.path in ("/", ""):
            self.path = "/dashboard.html"
        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/llm/evaluate":
            self._llm_evaluate()
            return
        self.send_error(404, "Not Found")

    def _json_response(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _candles(self, query: str) -> None:
        qs = urllib.parse.parse_qs(query)
        symbol = (qs.get("symbol") or ["BTCUSDT"])[0]
        exchange = (qs.get("exchange") or ["binance"])[0].lower()
        timeframe = _normalize_timeframe(
            (qs.get("timeframe") or qs.get("interval") or ["1h"])[0]
        )
        try:
            if "kucoin" in exchange:
                data = fetch_kucoin(symbol, KUCOIN_TF[timeframe])
                exchange = "kucoin"
            else:
                data = fetch_binance(symbol, BINANCE_TF[timeframe])
                exchange = "binance"
            self._json_response(
                200,
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "timeframe": timeframe,
                    "candles": data,
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._json_response(502, {"error": str(exc)})

    def _llm_evaluate(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json_response(400, {"ok": False, "error": "invalid_body", "message": "JSON erwartet"})
            return
        if not isinstance(payload, dict):
            self._json_response(400, {"ok": False, "error": "invalid_body", "message": "Objekt erwartet"})
            return

        # Cap confirms list size
        confirms = payload.get("confirms") or []
        if isinstance(confirms, list):
            payload["confirms"] = [str(c)[:200] for c in confirms[:8]]

        result = call_openrouter_evaluate(payload)
        code = 200 if result.get("ok") else (503 if result.get("error") == "missing_api_key" else 502)
        self._json_response(code, result)

    def log_message(self, fmt: str, *args) -> None:
        if args and str(args[0]).startswith("/api/"):
            super().log_message(fmt, *args)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    source = _llm_key_source()
    print(f"ATO dashboard: http://127.0.0.1:{PORT}/dashboard.html")
    print("Candle proxy:  /api/candles?symbol=BTCUSDT&exchange=binance&timeframe=1h")
    key_state = f"set via {source}" if source else "missing"
    print(f"LLM evaluate:  POST /api/llm/evaluate  model={EVAL_MODEL}  key={key_state}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
