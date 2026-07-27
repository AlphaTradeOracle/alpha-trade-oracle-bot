"""Provider-unabhaengiges LLM-Interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.llm.schemas import LLMUsage


class RawCompletion:
    """Rohantwort eines Providers, noch nicht validiert."""

    __slots__ = ("content", "usage")

    def __init__(self, content: str, usage: LLMUsage | None = None) -> None:
        self.content = content
        self.usage = usage or LLMUsage()


@runtime_checkable
class LLMProvider(Protocol):
    """Vertrag fuer jeden LLM-Anbieter."""

    name: str
    model: str

    async def complete(self, system_prompt: str, messages: list[dict[str, str]]) -> RawCompletion:
        """Chat-Completion ausfuehren und die Rohantwort zurueckgeben."""
        ...

    async def health_check(self) -> bool: ...

    async def close(self) -> None: ...
