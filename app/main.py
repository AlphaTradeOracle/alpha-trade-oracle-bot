"""FastAPI-Anwendung: Einstiegspunkt des API-Prozesses."""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import router as api_router
from app.container import build_container
from app.core.config import get_settings
from app.core.errors import AlphaTradeOracleError, SymbolNotFoundError
from app.core.logging import configure_logging, get_logger, set_correlation_id
from app.schemas.common import ErrorResponse

logger = get_logger(__name__)

CORRELATION_HEADER = "X-Request-ID"

OPENAPI_DESCRIPTION = """\
Analyse-API des Alpha Trade Oracle Bot.

Der Dienst berechnet technische Marktanalysen und Signale ueber mehrere
Timeframes. Er fuehrt **keine** Trades aus, greift nicht auf Wallets zu und gibt
keine Anlageberatung. Alle Ausgaben sind Einschaetzungen und enthalten einen
entsprechenden Hinweis.

Administrative Endpunkte erfordern den Header `X-Admin-Token`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Abhaengigkeiten beim Start aufbauen und beim Stoppen freigeben."""
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.app_env != "development")

    container = build_container(settings)
    app.state.container = container
    app.state.analysis_service = container.analysis_service
    app.state.backtest_service = container.backtest_service if settings.enable_backtesting else None
    app.state.health_service = container.health_service

    logger.info(
        "application_started",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )
    try:
        yield
    finally:
        await container.aclose()
        logger.info("application_stopped")


def create_app() -> FastAPI:
    """FastAPI-Anwendung erzeugen."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=OPENAPI_DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.include_router(api_router)
    _register_middleware(app)
    _register_exception_handlers(app)
    return app


def _register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def correlation_and_timing(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Correlation-ID setzen und Antwortzeit protokollieren."""
        incoming = request.headers.get(CORRELATION_HEADER)
        correlation_id = set_correlation_id(incoming)
        started = time.perf_counter()

        response = await call_next(request)

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers[CORRELATION_HEADER] = correlation_id

        # Healthchecks werden nur auf DEBUG protokolliert, sonst fluten sie das Log.
        log = logger.debug if request.url.path in ("/health", "/ready") else logger.info
        log(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(SymbolNotFoundError)
    async def handle_symbol_not_found(_request: Request, exc: SymbolNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(detail=str(exc), error_type=type(exc).__name__).model_dump(),
        )

    @app.exception_handler(AlphaTradeOracleError)
    async def handle_domain_error(_request: Request, exc: AlphaTradeOracleError) -> JSONResponse:
        logger.warning("domain_error", error=str(exc), error_type=type(exc).__name__)
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(detail=str(exc), error_type=type(exc).__name__).model_dump(),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        """Unerwartete Fehler nie im Klartext nach aussen geben.

        Details landen im Log, der Client erhaelt nur die Correlation-ID.
        """
        logger.error("unhandled_exception", error=str(exc), exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                detail="Interner Fehler. Details stehen im Anwendungsprotokoll.",
                error_type="InternalServerError",
            ).model_dump(),
        )


app = create_app()
