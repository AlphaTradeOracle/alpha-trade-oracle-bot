"""LLM-Service: Aufruf, Validierung, ein Korrektur-Retry, dann Fallback.

Die Schicht ist bewusst so gebaut, dass ein Ausfall oder eine ungueltige Antwort
das System nie blockiert: schlaegt die Validierung zweimal fehl, arbeitet der Bot
mit dem regelbasierten Text weiter.
"""

from __future__ import annotations

import json
import re
import time

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.enums import LLMRequestStatus
from app.core.logging import get_logger
from app.llm.base import LLMProvider
from app.llm.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_correction_prompt,
    build_user_prompt,
)
from app.llm.schemas import LLMAnalysisResponse, LLMCallResult, LLMUsage
from app.signals.types import SignalResult

logger = get_logger(__name__)

#: Entfernt ```json ... ``` Code-Fences, die Modelle trotz Anweisung oft setzen.
_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class LLMService:
    """Erzeugt eine validierte, sprachliche Zusammenfassung zu einem Signal."""

    def __init__(
        self, provider: LLMProvider | None = None, settings: Settings | None = None
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = provider

    @property
    def is_enabled(self) -> bool:
        return self._provider is not None and self._settings.enable_llm_analysis

    async def summarize(self, result: SignalResult) -> LLMCallResult:
        """Signal zusammenfassen lassen.

        Bei jedem Fehler wird ein ``LLMCallResult`` mit ``analysis=None``
        zurueckgegeben — nie eine Exception nach oben gereicht. Der Aufrufer
        entscheidet dann fuer den regelbasierten Text.
        """
        if not self.is_enabled:
            return LLMCallResult(
                analysis=None,
                provider=self._settings.llm_provider,
                model=self._settings.llm_model,
                prompt_version=PROMPT_VERSION,
                status=LLMRequestStatus.SKIPPED.value,
                duration_ms=0,
                error_message="LLM-Analyse ist nicht aktiv; es wird regelbasiert formuliert",
            )

        provider = self._provider
        if provider is None:  # pragma: no cover - durch is_enabled ausgeschlossen
            raise RuntimeError("LLM ist aktiv, aber kein Provider gesetzt")

        started = time.perf_counter()
        usage = LLMUsage()
        user_prompt = build_user_prompt(result)
        messages = [{"role": "user", "content": user_prompt}]
        validation_error: str | None = None

        for attempt in (1, 2):
            try:
                completion = await provider.complete(SYSTEM_PROMPT, messages)
            except Exception as exc:
                logger.warning(
                    "llm_call_failed",
                    symbol=result.symbol,
                    attempt=attempt,
                    error=str(exc),
                )
                return self._failure(
                    started, usage, LLMRequestStatus.ERROR, error_message=str(exc), attempts=attempt
                )

            usage = completion.usage

            try:
                analysis = self._parse(completion.content)
            except (ValidationError, ValueError) as exc:
                validation_error = _shorten(str(exc))
                logger.warning(
                    "llm_validation_failed",
                    symbol=result.symbol,
                    attempt=attempt,
                    error=validation_error,
                )
                if attempt == 1:
                    # Ein gezielter Korrekturversuch mit dem konkreten Fehler.
                    messages = [
                        {"role": "user", "content": user_prompt},
                        {"role": "assistant", "content": completion.content[:2000]},
                        {"role": "user", "content": build_correction_prompt(validation_error)},
                    ]
                    continue
                return self._failure(
                    started,
                    usage,
                    LLMRequestStatus.VALIDATION_FAILED,
                    validation_error=validation_error,
                    attempts=attempt,
                )

            status = LLMRequestStatus.SUCCESS if attempt == 1 else LLMRequestStatus.RETRY_SUCCESS
            logger.info(
                "llm_summary_created",
                symbol=result.symbol,
                provider=provider.name,
                model=provider.model,
                attempts=attempt,
                total_tokens=usage.total_tokens,
            )
            return LLMCallResult(
                analysis=analysis,
                provider=provider.name,
                model=provider.model,
                prompt_version=PROMPT_VERSION,
                status=status.value,
                duration_ms=_elapsed_ms(started),
                usage=usage,
                validation_error=validation_error,
                attempts=attempt,
            )

        # Unerreichbar, die Schleife kehrt in jedem Zweig zurueck.
        return self._failure(started, usage, LLMRequestStatus.ERROR, attempts=2)

    @staticmethod
    def _parse(content: str) -> LLMAnalysisResponse:
        """JSON aus der Rohantwort extrahieren und gegen das Schema pruefen."""
        cleaned = _FENCE_PATTERN.sub("", content.strip())

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            # Letzter Versuch: das erste vollstaendige JSON-Objekt herausschneiden.
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start == -1 or end <= start:
                raise ValueError("Antwort enthaelt kein JSON-Objekt") from None
            payload = json.loads(cleaned[start : end + 1])

        if not isinstance(payload, dict):
            raise ValueError(f"Erwartet wurde ein JSON-Objekt, erhalten: {type(payload).__name__}")

        return LLMAnalysisResponse.model_validate(payload)

    def _failure(
        self,
        started: float,
        usage: LLMUsage,
        status: LLMRequestStatus,
        *,
        validation_error: str | None = None,
        error_message: str | None = None,
        attempts: int = 1,
    ) -> LLMCallResult:
        provider_name = self._provider.name if self._provider else self._settings.llm_provider
        model_name = self._provider.model if self._provider else self._settings.llm_model
        return LLMCallResult(
            analysis=None,
            provider=provider_name,
            model=model_name,
            prompt_version=PROMPT_VERSION,
            status=status.value,
            duration_ms=_elapsed_ms(started),
            usage=usage,
            validation_error=validation_error,
            error_message=error_message,
            attempts=attempts,
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _shorten(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[:limit] + " ..."
