"""LLM-Anbindung ueber eine OpenAI-kompatible Chat-Completions-API.

Funktioniert mit OpenRouter, OpenAI selbst und jedem anderen Dienst, der
``POST /chat/completions`` im OpenAI-Format anbietet.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError, LLMError
from app.core.http import request_with_retry
from app.core.logging import get_logger
from app.llm.base import RawCompletion
from app.llm.schemas import LLMUsage

logger = get_logger(__name__)


class OpenAICompatibleProvider:
    """Implementierung von :class:`~app.llm.base.LLMProvider`."""

    def __init__(
        self, settings: Settings | None = None, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._settings = settings or get_settings()
        api_key = self._settings.llm_api_key.get_secret_value()
        if not api_key:
            raise ConfigurationError(
                "LLM_API_KEY ist nicht gesetzt.",
                detail="Entweder einen Key hinterlegen oder ENABLE_LLM_ANALYSIS=false setzen",
            )

        self.name = self._settings.llm_provider
        self.model = self._settings.llm_model

        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._settings.llm_base_url.rstrip("/"),
            timeout=httpx.Timeout(self._settings.llm_timeout_seconds),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # OpenRouter empfiehlt diese Header zur Zuordnung der Anwendung.
                "X-Title": self._settings.app_name,
            },
        )

    async def complete(self, system_prompt: str, messages: list[dict[str, str]]) -> RawCompletion:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
            "temperature": self._settings.llm_temperature,
            "max_tokens": self._settings.llm_max_tokens,
            # Erzwingt JSON, sofern der Anbieter es unterstuetzt. Wird es ignoriert,
            # faengt die Schemapruefung den Fehler ab.
            "response_format": {"type": "json_object"},
        }

        response = await request_with_retry(
            self._client,
            "POST",
            "/chat/completions",
            max_retries=1,  # LLM-Aufrufe sind teuer; nur ein Transport-Retry
            json=payload,
        )

        if response.status_code >= 400:
            raise LLMError(
                f"LLM-Anbieter hat mit HTTP {response.status_code} geantwortet.",
                detail=response.text[:300],
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError(
                "Antwort des LLM-Anbieters war kein gueltiges JSON.",
                detail=response.text[:200],
            ) from exc

        content = _extract_content(data)
        usage = _extract_usage(data)
        return RawCompletion(content=content, usage=usage)

    async def health_check(self) -> bool:
        try:
            response = await self._client.get("/models")
            return response.status_code < 400
        except Exception as exc:
            logger.warning("llm_health_check_failed", provider=self.name, error=str(exc))
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _extract_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("Antwort des LLM enthaelt kein 'choices'-Feld.", detail=str(data)[:200])

    message = choices[0].get("message") or {}
    content = message.get("content")

    # Manche Anbieter liefern Content als Liste von Bloecken.
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))

    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM hat eine leere Antwort geliefert.")
    return content


def _extract_usage(data: dict[str, Any]) -> LLMUsage:
    usage = data.get("usage") or {}
    return LLMUsage(
        prompt_tokens=_as_int(usage.get("prompt_tokens")),
        completion_tokens=_as_int(usage.get("completion_tokens")),
        total_tokens=_as_int(usage.get("total_tokens")),
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
