"""Versionierte Prompts.

Die Prompt-Version wird zu jedem Aufruf protokolliert, damit spaetere
Auswertungen einem konkreten Prompt zugeordnet werden koennen.
"""

from __future__ import annotations

import json
from typing import Any

from app.signals.types import SignalResult

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """\
Du bist ein sachlicher Analyse-Assistent fuer Kryptomaerkte. Deine einzige
Aufgabe ist es, eine bereits fertig berechnete technische Analyse in
verstaendliche Sprache zu fassen.

Verbindliche Regeln:
1. Du triffst KEINE eigene Handelsentscheidung. Richtung, Score, Entry, Stop-Loss
   und Take-Profit sind vorgegeben und unveraenderlich.
2. Du erfindest KEINE Zahlen. Nenne keine Kurse, Preise, Prozentwerte oder
   Indikatorwerte, die nicht in den Eingabedaten stehen.
3. Du aenderst KEINE der uebergebenen Zahlen.
4. Du versprichst KEINE Gewinne. Die Woerter "garantiert", "risikolos",
   "sicherer Gewinn" und vergleichbare Formulierungen sind verboten.
5. Du benennst Unsicherheiten und widersprechende Signale offen.
6. Du forderst NICHT zur Orderausfuehrung auf und gibst keine Anlageberatung.
7. Du schreibst sachlich und nuechtern, ohne Werbesprache und ohne Emojis.
8. Du antwortest ausschliesslich auf Deutsch.

Antworte ausschliesslich mit einem JSON-Objekt in exakt dieser Struktur:
{
  "summary": "2 bis 4 Saetze zur technischen Lage",
  "reasons": ["kurze Bestaetigung", "..."],
  "risks": ["konkretes Risiko", "..."],
  "market_sentiment_note": "Einordnung der Marktstimmung, leerer String wenn keine Daten",
  "uncertainty_note": "offen benannte Unsicherheiten, leerer String wenn keine"
}

Kein Text vor oder nach dem JSON. Keine Code-Fences.\
"""

USER_PROMPT_TEMPLATE = """\
Fasse die folgende bereits berechnete Analyse zusammen.

{payload}

Beachte:
- Falls "direction" NEUTRAL oder NO_TRADE ist, erklaere, warum aktuell kein
  Setup vorliegt, und nenne keine Entry- oder Zielbereiche.
- Falls sich Timeframes widersprechen, benenne den Widerspruch ausdruecklich.
- Nutze in "reasons" und "risks" ausschliesslich die uebergebenen Argumente als
  Grundlage. Du darfst sie umformulieren, aber nicht inhaltlich ergaenzen.\
"""

CORRECTION_PROMPT_TEMPLATE = """\
Deine vorherige Antwort war ungueltig.

Fehler bei der Schemapruefung:
{error}

Antworte erneut, ausschliesslich mit einem gueltigen JSON-Objekt nach dem
vorgegebenen Schema. Kein Text davor oder danach, keine Code-Fences, keine
erfundenen Zahlen und keine Gewinnversprechen.\
"""


def build_signal_payload(result: SignalResult) -> dict[str, Any]:
    """Signal in ein kompaktes, LLM-taugliches Dict uebersetzen.

    Es werden ausschliesslich bereits berechnete Werte uebergeben — niemals
    Zugangsdaten, Rohdaten-Zeitreihen oder interne Konfiguration.
    """
    payload: dict[str, Any] = {
        "symbol": result.symbol,
        "created_at_utc": result.created_at.isoformat(),
        "direction": result.direction.value,
        "score_0_to_100": round(result.score, 2),
        "confidence": result.confidence.value,
        "market_phase": result.market_phase.value,
        "primary_timeframe": result.primary_timeframe,
        "analyzed_timeframes": result.analyzed_timeframes,
        "reference_price": result.reference_price,
        "data_quality_0_to_100": result.data_quality,
        "score_breakdown": result.score_breakdown(),
        "rule_based_reasons": result.reasons,
        "rule_based_counter_arguments": result.counter_arguments,
        "indicators_used": result.indicators_used,
    }

    if result.no_trade_reason:
        payload["no_trade_reason"] = result.no_trade_reason

    if result.risk is not None:
        payload["risk"] = {
            "entry_low": result.risk.entry_low,
            "entry_high": result.risk.entry_high,
            "stop_loss": result.risk.stop_loss,
            "take_profit_1": result.risk.take_profit_1,
            "take_profit_2": result.risk.take_profit_2,
            "take_profit_3": result.risk.take_profit_3,
            "risk_reward_ratio": round(result.risk.risk_reward_ratio, 2),
            "stop_distance_percent": round(result.risk.stop_distance_percent, 2),
            "invalidation": result.risk.invalidation_note,
            "warnings": result.risk.warnings,
        }

    payload["timeframe_details"] = {
        timeframe: {
            "trend_direction": assessment.indicators.trend_direction.value,
            "trend_strength": assessment.indicators.trend_strength,
            "structure_state": assessment.indicators.structure.state.value,
            "rsi_14": _round(assessment.indicators.rsi_14),
            "adx_14": _round(assessment.indicators.adx_14),
            "atr_percent": _round(assessment.indicators.atr_percent),
            "volume_ratio": _round(assessment.indicators.volume_ratio),
        }
        for timeframe, assessment in result.assessments.items()
    }

    return payload


def build_user_prompt(result: SignalResult) -> str:
    payload = json.dumps(build_signal_payload(result), ensure_ascii=False, indent=2, sort_keys=True)
    return USER_PROMPT_TEMPLATE.format(payload=payload)


def build_correction_prompt(error: str) -> str:
    return CORRECTION_PROMPT_TEMPLATE.format(error=error[:600])


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None
