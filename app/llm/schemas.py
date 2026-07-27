"""Pydantic-Schema der erwarteten LLM-Antwort.

Das Schema erzwingt Textfelder und laesst bewusst keine Zahlenfelder zu. Selbst
wenn das Modell halluziniert, kann es damit keinen Kurs, kein Stop-Level und kein
Kursziel in die Nachricht bringen — alle Zahlen werden aus dem Signal-Objekt
formatiert.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

#: Formulierungen, die eine Gewinngarantie suggerieren und daher verboten sind.
FORBIDDEN_PATTERNS = (
    r"\bgarantiert",
    r"\bgarantie\b",
    r"\bsicherer gewinn",
    r"\brisikolos",
    r"\bguaranteed\b",
    r"\brisk[- ]free\b",
    r"\bkein risiko\b",
    r"\b100\s*%\s*(sicher|gewinn)",
)

MAX_SUMMARY_LENGTH = 700
MAX_ITEM_LENGTH = 200


class LLMAnalysisResponse(BaseModel):
    """Strukturierte Antwort des LLM zu einem bereits berechneten Signal."""

    model_config = {"extra": "forbid"}

    summary: str = Field(
        min_length=20,
        max_length=MAX_SUMMARY_LENGTH,
        description="Sachliche Zusammenfassung der technischen Lage in 2 bis 4 Saetzen.",
    )
    reasons: list[str] = Field(
        min_length=1,
        max_length=6,
        description="Kurze Bestaetigungen, die fuer das Signal sprechen.",
    )
    risks: list[str] = Field(
        min_length=1,
        max_length=6,
        description="Konkrete Risiken und Gegenargumente.",
    )
    market_sentiment_note: str = Field(
        default="",
        max_length=300,
        description="Einordnung der Marktstimmung. Leer, wenn keine Daten vorliegen.",
    )
    uncertainty_note: str = Field(
        default="",
        max_length=300,
        description="Offen benannte Unsicherheiten der Analyse.",
    )

    @field_validator("summary", "market_sentiment_note", "uncertainty_note")
    @classmethod
    def _check_forbidden_text(cls, value: str) -> str:
        _reject_promises(value)
        return value.strip()

    @field_validator("reasons", "risks")
    @classmethod
    def _check_items(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            stripped = item.strip()
            if not stripped:
                continue
            if len(stripped) > MAX_ITEM_LENGTH:
                raise ValueError(
                    f"Listeneintrag ist zu lang ({len(stripped)} Zeichen, "
                    f"maximal {MAX_ITEM_LENGTH})"
                )
            _reject_promises(stripped)
            cleaned.append(stripped)
        if not cleaned:
            raise ValueError("Liste enthaelt nach dem Bereinigen keine Eintraege")
        return cleaned


def _reject_promises(text: str) -> None:
    lowered = text.lower()
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, lowered):
            raise ValueError(
                f"Antwort enthaelt ein unzulaessiges Gewinnversprechen (Muster: {pattern!r})"
            )


class LLMUsage(BaseModel):
    """Tokenverbrauch eines Aufrufs."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class LLMCallResult(BaseModel):
    """Ergebnis eines LLM-Aufrufs inkl. Protokolldaten."""

    model_config = {"arbitrary_types_allowed": True}

    analysis: LLMAnalysisResponse | None
    provider: str
    model: str
    prompt_version: str
    status: str
    duration_ms: int
    usage: LLMUsage = Field(default_factory=LLMUsage)
    validation_error: str | None = None
    error_message: str | None = None
    attempts: int = 1

    @property
    def succeeded(self) -> bool:
        return self.analysis is not None
