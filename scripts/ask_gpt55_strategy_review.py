"""One-shot strategy + Telegram UX review via OpenRouter GPT-5.5 (stdlib only)."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MODEL = os.environ.get("REVIEW_MODEL", "openai/gpt-5.5")
BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OUT = Path(os.environ.get("REVIEW_OUT", "/tmp/gpt55_strategy_review.md"))


def _load_env_files() -> None:
    for candidate in (
        Path("/app/.env"),
        Path("/opt/alpha-trade-oracle-bot/.env"),
        Path.cwd() / ".env",
    ):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


CONTEXT = """
# Alpha Trade Oracle Bot — aktueller Stand (Code/Config)

## Signal-Engine / Filter
- Score-min: 75 (SIGNAL_MIN_SCORE), nur STRONG (SIGNAL_REQUIRE_STRONG=true)
- Short-Spiegel: score <= 25 (SIGNAL_SHORT_MAX_SCORE)
- RSI long max 75, short min 25
- RANGE-Markt blockiert (SIGNAL_BLOCK_RANGE_MARKET)
- min ADX 20
- Cooldown 120 Min, Expiry = 4x Timeframe
- Multi-TF: 15m,1h,4h,1d — Primary 1h
- Universe: Top 100 Market-Cap-Rank, KuCoin primary

## Risk / Exits (neu)
- TP Multiples: 2.0 / 4.0 / 6.0 R (DEFAULT_TP_MULTIPLIERS)
- Scale-out: 33/33/34
- BE nach TP1 (paper_move_stop_to_breakeven)
- Stop via ATR/Struktur, Entry-Zone ±0.25 ATR
- Paper: $5000 Start, $100 Margin, 10x Leverage, 0.1% Fee

## Paper-Ergebnis (nach Rebuild auf 2/4/6)
- Equity ~$4912, realized ~-$88
- Viele Full-Stops, wenige Winner; XAVA/SOON oft MTM-positiv
- Exit-Sim auf denselben Entries: fixed 2/4/6 best (+15.79) > 2/3.5/5.5 (+13.45) > tight > trail@TP2 > trail@TP1
- SCUSDT nicht auf KuCoin → Replay skip

## Architektur-Constraints
- LLM heute NUR Telegram-Prosa (keine Trade-Entscheidung, keine Zahlen erfinden)
- Chart separat (matplotlib PNG), dann Text-Nachricht
- Delivery: erst Photo, dann Text (zwei Messages) — Caption am Photo waere moeglich aber aktuell nicht genutzt

## Telegram heute (formatting.py)
Lange MarkdownV2-Nachricht: Brand, Asset, Signal, Staerke, Konfidenz, Marktphase,
Entry, SL, TP1-3, RR, Positionsgroesse, Trends, LLM-Einordnung, Bestaetigungen,
Risiken, Ungueltig bei, Timeframes/Datenqualitaet, Zeitstempel, Disclaimer.
"""

SYSTEM = """Du bist ein kritischer Quant/Product-Advisor fuer einen Crypto-Signal-Bot.
Antworte auf Deutsch, direkt, ohne Fluff. Keine Gewinnversprechen.
Zahlen/Setup nur aus dem Kontext ableiten; wenn unsicher, sag es.
Strukturiere klar mit Ueberschriften."""

USER = f"""
Pruefe die Strategie und schlage Verbesserungen vor, um Profit/Expectancy zu maximieren
(nicht Signal-Volumen).

{CONTEXT}

Liefer bitte:

## 1. Strategie-Urteil (5-8 Saetze)
## 2. Top-Schwaechen die PnL killen
## 3. Priorisierte Fixes (P0/P1/P2) — je: Aenderung, Warum, Validierung, Effort S/M/L
## 4. Top 5 Experimente (Impact/Effort)
## 5. Was wir NICHT tun sollten
## 6. Cleanes Telegram-Signal (Entwurf)
   - Layout: Chart oben, Text unten (als Photo-Caption ODER kompakte Folge-Nachricht)
   - Schreib den konkreten Caption-/Nachrichtentext fuer ein Beispiel:
     XAVA/USDT STRONG SHORT, Score 82, Entry 0.230-0.235, SL 0.248, TP1/2/3 aus 2/4/6R,
     BE nach TP1, Scale 33/33/34, 1h primary
   - MarkdownV2-taugliche Struktur skizzieren (ohne Escape-Spam), max ~800 Zeichen Caption-Ziel
   - Visuelle Regeln: was aufs Chart, was in den Text, was weglassen
"""


def main() -> int:
    _load_env_files()
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        print("LLM_API_KEY missing", file=sys.stderr)
        return 1

    payload = {
        "model": MODEL,
        "temperature": 0.3,
        "max_tokens": 4500,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/AlphaTradeOracle/alpha-trade-oracle-bot",
            "X-Title": "Alpha Trade Oracle Strategy Review",
        },
        method="POST",
    )
    print(f"Calling {MODEL} via OpenRouter ...", flush=True)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")[:800]
        print(f"HTTP {exc.code}: {err}", file=sys.stderr)
        return 1

    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        OUT.write_text(json.dumps(body, indent=2)[:5000], encoding="utf-8")
        print("Unexpected response shape; raw saved", file=sys.stderr)
        return 1

    OUT.write_text(text, encoding="utf-8")
    usage = body.get("usage") or {}
    print(f"OK -> {OUT}  usage={usage}", flush=True)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
