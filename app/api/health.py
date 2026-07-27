"""Health-, Readiness- und Versions-Endpunkte."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import HealthServiceDep, SettingsDep
from app.schemas.common import HealthResponse, ReadinessResponse, VersionResponse

router = APIRouter(tags=["monitoring"])


@router.get("/health", response_model=HealthResponse, summary="Liveness-Check")
async def health(health_service: HealthServiceDep) -> HealthResponse:
    """Prueft nur den Prozess selbst.

    Bewusst ohne externe Abhaengigkeiten: sonst wuerde ein Redis-Ausfall einen
    Container-Neustart durch den Orchestrator ausloesen.
    """
    return HealthResponse(**await health_service.liveness())


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness-Check")
async def ready(health_service: HealthServiceDep, response: Response) -> ReadinessResponse:
    """Prueft alle Abhaengigkeiten.

    Antwortet mit HTTP 503, wenn eine erforderliche Komponente nicht bereit ist,
    damit Load Balancer den Container aus der Rotation nehmen.
    """
    report = await health_service.readiness()
    if not report.is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(**report.as_dict())


@router.get("/version", response_model=VersionResponse, summary="Versionsinformationen")
async def version(settings: SettingsDep) -> VersionResponse:
    return VersionResponse(
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        market_data_provider=settings.market_data_provider,
        llm_enabled=settings.llm_configured,
        sentiment_enabled=settings.enable_sentiment,
        backtesting_enabled=settings.enable_backtesting,
    )
