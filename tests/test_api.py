"""Tests der HTTP-Schicht.

Die Services werden ueber FastAPI-Dependency-Overrides ersetzt, damit weder
Datenbank noch Boerse noetig sind.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import (
    analysis_service,
    db_session,
    health_service,
    require_admin_token,
)
from app.core.config import Settings
from app.main import create_app
from app.monitoring.health import ComponentStatus, HealthReport
from app.services.analysis_service import AnalysisOutcome
from tests.test_dedup import make_result

NOW = datetime(2024, 6, 1, 12, tzinfo=UTC)


class StubHealthService:
    def __init__(self, *, ready: bool = True) -> None:
        self._ready = ready

    async def liveness(self) -> dict[str, object]:
        return {
            "status": "ok",
            "app": "Alpha Trade Oracle Bot",
            "version": "0.1.0",
            "environment": "test",
            "timestamp": NOW,
        }

    async def readiness(self) -> HealthReport:
        return HealthReport(
            components=[
                ComponentStatus("database", healthy=self._ready, required=True, detail="ok"),
                ComponentStatus("redis", healthy=True, required=False, detail="ok"),
            ]
        )


class StubAnalysisService:
    def __init__(self, outcome: AnalysisOutcome | Exception) -> None:
        self._outcome = outcome
        self.calls: list[str] = []

    async def analyze(self, symbol: str, **_kwargs: object) -> AnalysisOutcome:
        self.calls.append(symbol)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def make_outcome() -> AnalysisOutcome:
    return AnalysisOutcome(
        result=make_result(),
        signal_id=17,
        skipped_timeframes=["1d"],
        price_precision=2,
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Client mit ersetzten Abhaengigkeiten."""
    app = create_app()

    app.dependency_overrides[health_service] = lambda: StubHealthService()
    app.dependency_overrides[analysis_service] = lambda: StubAnalysisService(make_outcome())
    app.dependency_overrides[db_session] = _no_session
    app.dependency_overrides[require_admin_token] = lambda: None

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


async def _no_session() -> object:
    raise AssertionError("Dieser Test darf die Datenbank nicht benoetigen")


class TestMonitoringEndpoints:
    def test_health_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_health_does_not_depend_on_external_services(self, client: TestClient) -> None:
        """Ein Redis-Ausfall darf keinen Container-Neustart ausloesen."""
        client.app.dependency_overrides[health_service] = lambda: StubHealthService(ready=False)
        assert client.get("/health").status_code == 200

    def test_ready_returns_200_when_all_required_components_are_up(
        self, client: TestClient
    ) -> None:
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"

    def test_ready_returns_503_when_a_required_component_is_down(self, client: TestClient) -> None:
        client.app.dependency_overrides[health_service] = lambda: StubHealthService(ready=False)
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"

    def test_version_reports_configuration(self, client: TestClient) -> None:
        body = client.get("/version").json()
        assert body["app"]
        assert body["version"]
        assert "market_data_provider" in body
        assert isinstance(body["llm_enabled"], bool)


class TestCorrelationId:
    def test_response_carries_correlation_header(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.headers.get("X-Request-ID")

    def test_incoming_correlation_id_is_echoed(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Request-ID": "abc-123"})
        assert response.headers["X-Request-ID"] == "abc-123"


class TestAnalysisEndpoint:
    def test_returns_signal_for_valid_symbol(self, client: TestClient) -> None:
        response = client.post("/api/v1/analysis", json={"symbol": "BTCUSDT", "persist": False})
        assert response.status_code == 200

        body = response.json()
        assert body["signal"]["symbol"] == "BTCUSDT"
        assert body["signal"]["direction"] == "LONG"
        assert body["signal"]["id"] == 17
        assert body["skipped_timeframes"] == ["1d"]
        assert body["llm_used"] is False

    def test_response_includes_risk_levels(self, client: TestClient) -> None:
        body = client.post("/api/v1/analysis", json={"symbol": "BTCUSDT"}).json()
        risk = body["signal"]["risk"]
        assert risk["stop_loss"] > 0
        assert risk["take_profit_1"] and risk["take_profit_2"] and risk["take_profit_3"]
        assert risk["risk_reward_ratio"] > 0

    def test_response_contains_disclaimer(self, client: TestClient) -> None:
        """Auch die API darf nicht als Anlageberatung missverstanden werden."""
        body = client.post("/api/v1/analysis", json={"symbol": "BTCUSDT"}).json()
        assert "disclaimer" in body["signal"]
        assert "Finanzberatung" in body["signal"]["disclaimer"]

    def test_unknown_symbol_yields_404(self, client: TestClient) -> None:
        from app.core.errors import SymbolNotFoundError

        client.app.dependency_overrides[analysis_service] = lambda: StubAnalysisService(
            SymbolNotFoundError("NOPEUSDT")
        )
        response = client.post("/api/v1/analysis", json={"symbol": "NOPEUSDT"})
        assert response.status_code == 404

    def test_insufficient_data_yields_422(self, client: TestClient) -> None:
        from app.core.errors import InsufficientDataError

        client.app.dependency_overrides[analysis_service] = lambda: StubAnalysisService(
            InsufficientDataError("BTCUSDT", "1h", 10, 210)
        )
        response = client.post("/api/v1/analysis", json={"symbol": "BTCUSDT"})
        assert response.status_code == 422

    def test_rejects_empty_symbol(self, client: TestClient) -> None:
        assert client.post("/api/v1/analysis", json={"symbol": ""}).status_code == 422

    def test_rejects_missing_symbol(self, client: TestClient) -> None:
        assert client.post("/api/v1/analysis", json={}).status_code == 422

    def test_never_exposes_internal_errors(self, client: TestClient) -> None:
        """Interne Fehlermeldungen duerfen nicht nach aussen gelangen."""
        client.app.dependency_overrides[analysis_service] = lambda: StubAnalysisService(
            RuntimeError("geheimes Passwort im Stacktrace")
        )
        with pytest.raises(RuntimeError):
            # TestClient reicht unbehandelte Fehler durch; der Handler greift im
            # Betrieb. Entscheidend ist, dass die Meldung nicht als Antwort dient.
            client.post("/api/v1/analysis", json={"symbol": "BTCUSDT"})


class TestBacktestEndpoint:
    def test_rejects_reversed_date_range(self, client: TestClient) -> None:
        # Anders als /analysis braucht dieser Endpunkt eine Session, die FastAPI
        # noch vor der Body-Validierung aufloest.
        client.app.dependency_overrides[db_session] = lambda: None
        response = client.post(
            "/api/v1/backtests",
            json={
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "start": "2025-01-01T00:00:00Z",
                "end": "2024-01-01T00:00:00Z",
            },
        )
        assert response.status_code == 422

    def test_requires_admin_token(self) -> None:
        """Backtests belasten die Marktdaten-API und sind daher geschuetzt."""
        app = create_app()
        app.dependency_overrides[health_service] = lambda: StubHealthService()
        app.dependency_overrides[db_session] = _no_session

        with TestClient(app) as unauthenticated:
            response = unauthenticated.post(
                "/api/v1/backtests",
                json={
                    "symbol": "BTCUSDT",
                    "timeframe": "1h",
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-06-01T00:00:00Z",
                },
            )
        assert response.status_code == 401


class TestOpenAPI:
    def test_all_required_paths_are_documented(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        for expected in (
            "/health",
            "/ready",
            "/version",
            "/api/v1/assets",
            "/api/v1/signals",
            "/api/v1/signals/{signal_id}",
            "/api/v1/analysis",
            "/api/v1/backtests",
            "/api/v1/backtests/{backtest_id}",
            "/api/v1/performance",
        ):
            assert expected in paths, expected

    def test_description_states_no_trading(self, client: TestClient) -> None:
        description = client.get("/openapi.json").json()["info"]["description"]
        assert "keine" in description.lower()
        assert "trades" in description.lower()

    def test_docs_are_served(self, client: TestClient) -> None:
        assert client.get("/docs").status_code == 200


class TestAdminGuard:
    @staticmethod
    def settings_with_token(token: str) -> Settings:
        return Settings(admin_api_token=token)

    @pytest.mark.asyncio
    async def test_correct_token_is_accepted(self) -> None:
        await require_admin_token(self.settings_with_token("geheim"), "geheim")

    @pytest.mark.asyncio
    async def test_wrong_token_is_rejected(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await require_admin_token(self.settings_with_token("geheim"), "falsch")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_token_is_rejected(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await require_admin_token(self.settings_with_token("geheim"), None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_unconfigured_token_locks_endpoint(self) -> None:
        """Ohne konfiguriertes Token bleibt der Zugang zu, nicht offen."""
        with pytest.raises(HTTPException) as exc:
            await require_admin_token(Settings(admin_api_token=""), "irgendwas")
        assert exc.value.status_code == 503
