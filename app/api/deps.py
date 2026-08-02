"""FastAPI-Dependencies: Session, Services und Admin-Absicherung."""

from __future__ import annotations

import secrets
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.database.session import get_db_session
from app.market_data.base import MarketDataProvider
from app.monitoring.health import HealthService
from app.services.analysis_service import AnalysisService
from app.services.backtest_service import BacktestService
from app.services.paper_trading_service import PaperTradingService


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


def paper_trading_service(request: Request) -> PaperTradingService:
    service = getattr(request.app.state, "paper_trading", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Paper-Trading ist in dieser Instanz nicht aktiviert.",
        )
    return service  # type: ignore[no-any-return]


def market_data_provider(request: Request) -> MarketDataProvider:
    provider = getattr(request.app.state, "provider", None)
    if provider is None:
        container = getattr(request.app.state, "container", None)
        provider = getattr(container, "provider", None) if container is not None else None
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Marktdaten-Provider ist nicht initialisiert.",
        )
    return provider  # type: ignore[no-any-return]


def paper_price_provider(request: Request) -> MarketDataProvider:
    """Perp (or spot) feed used for paper fills / open marks."""
    provider = getattr(request.app.state, "paper_price_provider", None)
    if provider is None:
        container = getattr(request.app.state, "container", None)
        provider = (
            getattr(container, "paper_price_provider", None) if container is not None else None
        )
    if provider is None:
        return market_data_provider(request)
    return provider  # type: ignore[no-any-return]


def universe_providers(request: Request) -> dict[str, MarketDataProvider]:
    providers = getattr(request.app.state, "universe_providers", None)
    if providers is None:
        container = getattr(request.app.state, "container", None)
        providers = getattr(container, "universe_providers", None) if container is not None else None
    return providers or {}


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
PaperTradingDep = Annotated[PaperTradingService, Depends(paper_trading_service)]
ProviderDep = Annotated[MarketDataProvider, Depends(market_data_provider)]
PaperPriceProviderDep = Annotated[MarketDataProvider, Depends(paper_price_provider)]
UniverseProvidersDep = Annotated[dict[str, MarketDataProvider], Depends(universe_providers)]
HealthServiceDep = Annotated[HealthService, Depends(health_service)]
AdminGuard = Depends(require_admin_token)
