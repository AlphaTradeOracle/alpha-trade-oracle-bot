"""Tests der LLM-Schicht: Schema-Validierung, Retry, regelbasierter Fallback."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.llm.base import RawCompletion
from app.llm.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_correction_prompt,
    build_user_prompt,
)
from app.llm.schemas import LLMAnalysisResponse, LLMUsage
from app.llm.service import LLMService
from tests.test_dedup import make_result

#: Die Testumgebung deaktiviert das LLM global; fuer diese Tests wird es
#: gezielt eingeschaltet.
LLM_SETTINGS = Settings(enable_llm_analysis=True)


def make_service(provider: object | None) -> LLMService:
    return LLMService(provider, settings=LLM_SETTINGS)  # type: ignore[arg-type]


VALID_PAYLOAD = {
    "summary": (
        "Die Lage ist ueberwiegend konstruktiv. Der Trend zeigt aufwaerts, "
        "das Momentum laesst jedoch nach."
    ),
    "reasons": ["EMA20 ueber EMA50", "Volumen steigt"],
    "risks": ["Widerstand in Reichweite"],
    "market_sentiment_note": "Neutral bis leicht positiv.",
    "uncertainty_note": "Die Datenlage im Tages-Timeframe ist noch duenn.",
}


class TestResponseSchema:
    def test_accepts_valid_response(self) -> None:
        response = LLMAnalysisResponse.model_validate(VALID_PAYLOAD)
        assert response.reasons == ["EMA20 ueber EMA50", "Volumen steigt"]

    def test_rejects_missing_summary(self) -> None:
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "summary"}
        with pytest.raises(ValidationError):
            LLMAnalysisResponse.model_validate(payload)

    def test_rejects_too_short_summary(self) -> None:
        with pytest.raises(ValidationError):
            LLMAnalysisResponse.model_validate({**VALID_PAYLOAD, "summary": "kurz"})

    def test_rejects_empty_reasons(self) -> None:
        with pytest.raises(ValidationError):
            LLMAnalysisResponse.model_validate({**VALID_PAYLOAD, "reasons": []})

    def test_rejects_extra_fields(self) -> None:
        """Zusaetzliche Felder koennten Zahlen einschmuggeln."""
        with pytest.raises(ValidationError):
            LLMAnalysisResponse.model_validate({**VALID_PAYLOAD, "stop_loss": 65_900})

    def test_schema_has_no_numeric_fields(self) -> None:
        """Das Schema darf keine Zahlenfelder anbieten."""
        for field in LLMAnalysisResponse.model_fields.values():
            assert field.annotation in (str, list[str]), field.annotation

    def test_strips_whitespace(self) -> None:
        response = LLMAnalysisResponse.model_validate(
            {**VALID_PAYLOAD, "reasons": ["  mit Rand  ", "", "   "]}
        )
        assert response.reasons == ["mit Rand"]

    def test_rejects_too_long_list_item(self) -> None:
        with pytest.raises(ValidationError, match="zu lang"):
            LLMAnalysisResponse.model_validate({**VALID_PAYLOAD, "reasons": ["x" * 250]})

    def test_rejects_too_many_list_items(self) -> None:
        with pytest.raises(ValidationError):
            LLMAnalysisResponse.model_validate(
                {**VALID_PAYLOAD, "risks": [f"Risiko {i}" for i in range(10)]}
            )

    def test_optional_notes_default_to_empty(self) -> None:
        response = LLMAnalysisResponse.model_validate(
            {
                "summary": VALID_PAYLOAD["summary"],
                "reasons": ["Grund"],
                "risks": ["Risiko"],
            }
        )
        assert response.market_sentiment_note == ""
        assert response.uncertainty_note == ""


class TestProfitPromiseRejection:
    @pytest.mark.parametrize(
        "text",
        [
            "Dieser Trade ist garantiert profitabel und daher zu empfehlen.",
            "Ein risikoloser Einstieg mit sehr hoher Trefferwahrscheinlichkeit.",
            "This setup is guaranteed to work out within the next days.",
            "Ein risk-free Setup mit klarer Struktur und gutem Verhaeltnis.",
            "Hier besteht kein Risiko, der Trend ist vollstaendig intakt.",
            "Eine 100 % sichere Gelegenheit mit klarer Struktur und Volumen.",
        ],
    )
    def test_rejects_guarantee_in_summary(self, text: str) -> None:
        with pytest.raises(ValidationError, match="Gewinnversprechen"):
            LLMAnalysisResponse.model_validate({**VALID_PAYLOAD, "summary": text})

    def test_rejects_guarantee_in_reasons(self) -> None:
        with pytest.raises(ValidationError, match="Gewinnversprechen"):
            LLMAnalysisResponse.model_validate(
                {**VALID_PAYLOAD, "reasons": ["garantierter Gewinn"]}
            )

    def test_rejects_guarantee_in_uncertainty_note(self) -> None:
        with pytest.raises(ValidationError, match="Gewinnversprechen"):
            LLMAnalysisResponse.model_validate(
                {**VALID_PAYLOAD, "uncertainty_note": "Keine, der Trade ist risikolos."}
            )


class TestPrompts:
    def test_system_prompt_states_all_hard_rules(self) -> None:
        """Alle im Auftrag geforderten Verbote muessen im Prompt stehen."""
        lowered = SYSTEM_PROMPT.lower()
        for expected in (
            "keine anlageberatung",
            "erfindest keine zahlen",
            "versprichst keine gewinne",
            "keine eigene handelsentscheidung",
            "orderausfuehrung",
            "unsicherheiten",
            "json",
        ):
            assert expected in lowered, expected

    def test_user_prompt_contains_computed_values_only(self) -> None:
        result = make_result()
        prompt = build_user_prompt(result)
        assert result.symbol in prompt
        assert result.direction.value in prompt

    def test_user_prompt_is_valid_json_payload(self) -> None:
        """Der Prompt uebergibt strukturierte Daten, keinen Freitext."""
        prompt = build_user_prompt(make_result())
        start = prompt.find("{")
        assert start != -1
        json.loads(prompt[start : prompt.rfind("}") + 1])

    def test_correction_prompt_names_the_error(self) -> None:
        prompt = build_correction_prompt("reasons: Liste ist leer")
        assert "reasons: Liste ist leer" in prompt
        assert "JSON" in prompt

    def test_prompt_version_is_set(self) -> None:
        assert PROMPT_VERSION


class StubProvider:
    """LLM-Provider-Stub mit vorgegebenen Antworten."""

    name = "stub"
    model = "stub-model"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, system_prompt: str, messages: list[dict[str, str]]) -> RawCompletion:
        self.calls.append(messages)
        if not self._responses:
            raise AssertionError("Mehr Aufrufe als vorgesehene Antworten")
        return RawCompletion(
            self._responses.pop(0),
            LLMUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class FailingProvider:
    name = "failing"
    model = "failing-model"

    async def complete(self, system_prompt: str, messages: list[dict[str, str]]) -> RawCompletion:
        raise ConnectionError("LLM nicht erreichbar")

    async def health_check(self) -> bool:
        return False

    async def close(self) -> None:
        return None


class TestLLMService:
    @pytest.mark.asyncio
    async def test_returns_analysis_on_valid_response(self) -> None:
        provider = StubProvider([json.dumps(VALID_PAYLOAD)])
        service = make_service(provider)

        result = await service.summarize(make_result())

        assert result.analysis is not None
        assert result.status == "success"
        assert result.attempts == 1
        assert result.usage.total_tokens == 150

    @pytest.mark.asyncio
    async def test_retries_once_with_correction_prompt(self) -> None:
        """Erst ein korrigierender Versuch, dann der Fallback."""
        provider = StubProvider(['{"summary": "zu kurz"}', json.dumps(VALID_PAYLOAD)])
        service = make_service(provider)

        result = await service.summarize(make_result())

        assert result.analysis is not None
        assert result.status == "retry_success"
        assert result.attempts == 2
        assert len(provider.calls) == 2

        # Der zweite Aufruf muss den konkreten Schemafehler zurueckspielen,
        # sonst kann das Modell nicht gezielt korrigieren.
        correction = provider.calls[1][-1]["content"]
        assert "ungueltig" in correction
        assert "summary" in correction

    @pytest.mark.asyncio
    async def test_falls_back_after_second_failure(self) -> None:
        provider = StubProvider(['{"bad": 1}', '{"still": "bad"}'])
        service = make_service(provider)

        result = await service.summarize(make_result())

        assert result.analysis is None
        assert result.status == "validation_failed"
        assert result.validation_error
        assert result.attempts == 2

    @pytest.mark.asyncio
    async def test_handles_non_json_response(self) -> None:
        provider = StubProvider(["Das ist kein JSON.", "Weiterhin kein JSON."])
        service = make_service(provider)

        result = await service.summarize(make_result())
        assert result.analysis is None
        assert result.validation_error

    @pytest.mark.asyncio
    async def test_extracts_json_from_code_fence(self) -> None:
        """Manche Modelle umschliessen JSON mit ```json-Bloecken."""
        fenced = f"```json\n{json.dumps(VALID_PAYLOAD)}\n```"
        service = make_service(StubProvider([fenced]))

        result = await service.summarize(make_result())
        assert result.analysis is not None

    @pytest.mark.asyncio
    async def test_provider_error_does_not_raise(self) -> None:
        """Ein LLM-Ausfall darf die Analyse nicht abbrechen."""
        result = await make_service(FailingProvider()).summarize(make_result())

        assert result.analysis is None
        assert result.status == "error"
        assert result.error_message

    @pytest.mark.asyncio
    async def test_service_without_provider_is_skipped_not_failed(self) -> None:
        """Ein bewusst deaktiviertes LLM darf keine Fehlerstatistik erzeugen."""
        service = make_service(None)
        assert service.is_enabled is False

        result = await service.summarize(make_result())
        assert result.analysis is None
        assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_feature_flag_disables_service_despite_provider(self) -> None:
        service = LLMService(
            StubProvider([json.dumps(VALID_PAYLOAD)]),
            settings=Settings(enable_llm_analysis=False),
        )
        assert service.is_enabled is False

        result = await service.summarize(make_result())
        assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_logs_provider_model_and_prompt_version(self) -> None:
        provider = StubProvider([json.dumps(VALID_PAYLOAD)])
        result = await make_service(provider).summarize(make_result())

        assert result.provider == "stub"
        assert result.model == "stub-model"
        assert result.prompt_version == PROMPT_VERSION
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_rejects_response_with_profit_promise(self) -> None:
        payload = {**VALID_PAYLOAD, "summary": "Dieser Trade ist garantiert erfolgreich."}
        service = make_service(StubProvider([json.dumps(payload), json.dumps(payload)]))

        result = await service.summarize(make_result())
        assert result.analysis is None
        assert result.validation_error is not None
        assert "Gewinnversprechen" in result.validation_error
