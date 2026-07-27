"""Erzeugung des LLM-Service. Fehlt der Key, wird der Service ohne Provider gebaut."""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError
from app.core.logging import get_logger
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.service import LLMService

logger = get_logger(__name__)

#: Alle registrierten Provider sprechen das OpenAI-kompatible Protokoll.
_OPENAI_COMPATIBLE = frozenset({"openrouter", "openai", "groq", "together", "deepseek", "custom"})


def create_llm_service(settings: Settings | None = None) -> LLMService:
    """LLM-Service erzeugen.

    Ist die LLM-Analyse abgeschaltet oder kein Key gesetzt, wird ein Service
    ohne Provider zurueckgegeben. Er meldet dann konsistent einen Fehlschlag,
    sodass der Aufrufer den regelbasierten Text verwendet.
    """
    cfg = settings or get_settings()

    if not cfg.enable_llm_analysis:
        logger.info("llm_disabled_by_config")
        return LLMService(None, cfg)

    if not cfg.llm_api_key.get_secret_value():
        logger.info("llm_disabled_missing_api_key")
        return LLMService(None, cfg)

    provider_key = cfg.llm_provider.lower().strip()
    if provider_key not in _OPENAI_COMPATIBLE:
        raise ConfigurationError(
            f"Unbekannter LLM-Provider: {cfg.llm_provider!r}.",
            detail=f"Unterstuetzt: {', '.join(sorted(_OPENAI_COMPATIBLE))}",
        )

    provider = OpenAICompatibleProvider(cfg)
    logger.info("llm_provider_created", provider=provider.name, model=provider.model)
    return LLMService(provider, cfg)
