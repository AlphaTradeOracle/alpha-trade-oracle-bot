"""FastAPI-Dependencies: Session, Services und Admin-Absicherung."""

from __future__ import annotations

import secrets
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.database.session import get_db_session
from app.monitoring.health import HealthService
from app.services.analysis_service import AnalysisService
from app.services.backtest_service import BacktestService


async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


def settings_dependency() -> Settings:
    return get_settings()


def analysis_service(request: Request) -> AnalysisService:
    """AnalysisService aus dem Anwendungszustand holen."""
    service = getattr(request.app.state, "analysis_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Der Analyse-Service ist nicht initialisiert.",
        )
    return service  # type: ignore[no-any-return]


def backtest_service(request: Request) -> BacktestService:
    service = getattr(request.app.state, "backtest_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backtesting ist in dieser Instanz nicht aktiviert.",
        )
    return service  # type: ignore[no-any-return]


def health_service(request: Request) -> HealthService:
    service = getattr(request.app.state, "health_service", None)
    if service is None:
        return HealthService()
    return service  # type: ignore[no-any-return]


async def require_admin_token(
    settings: Annotated[Settings, Depends(settings_dependency)],
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> None:
    """Administrative Endpunkte absichern.

    Ist kein Token konfiguriert, werden die Endpunkte gesperrt statt offen
    gelassen — ein leerer Token darf nie Zugang gewaehren.
    """
    expected = settings.admin_api_token.get_secret_value()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "ADMIN_API_TOKEN ist nicht konfiguriert. Administrative Endpunkte "
                "sind deshalb gesperrt."
            ),
        )
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungueltiger oder fehlender X-Admin-Token.",
        )


SessionDep = Annotated[AsyncSession, Depends(db_session)]
SettingsDep = Annotated[Settings, Depends(settings_dependency)]
AnalysisServiceDep = Annotated[AnalysisService, Depends(analysis_service)]
BacktestServiceDep = Annotated[BacktestService, Depends(backtest_service)]
HealthServiceDep = Annotated[HealthService, Depends(health_service)]
AdminGuard = Depends(require_admin_token)
