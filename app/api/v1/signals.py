"""Endpunkte fuer Assets, Signale, Analysen und Auswertungen."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.api.deps import AdminGuard, AnalysisServiceDep, SessionDep
from app.core.enums import SignalDirection
from app.core.errors import AlphaTradeOracleError, SymbolNotFoundError
from app.core.logging import get_logger, set_correlation_id
from app.database.session import session_scope
from app.repositories.asset_repository import AssetRepository
from app.repositories.signal_repository import SignalRepository
from app.schemas.common import AssetResponse
from app.schemas.signal import (
    AnalysisRequest,
    AnalysisResponse,
    PerformanceResponse,
    SignalResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["signals"])


@router.get("/assets", response_model=list[AssetResponse], summary="Bekannte Instrumente")
async def list_assets(session: SessionDep) -> list[AssetResponse]:
    assets = await AssetRepository(session).list_active()
    return [AssetResponse.model_validate(asset) for asset in assets]


@router.get("/signals", response_model=list[SignalResponse], summary="Signale auflisten")
async def list_signals(
    session: SessionDep,
    symbol: Annotated[str | None, Query(description="Filter auf ein Symbol")] = None,
    direction: Annotated[SignalDirection | None, Query(description="Filter auf Richtung")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SignalResponse]:
    signals = await SignalRepository(session).list_recent(
        symbol=symbol, direction=direction, limit=limit, offset=offset
    )
    if not signals:
        return []

    symbols = await AssetRepository(session).get_symbols_by_ids(
        [signal.asset_id for signal in signals]
    )
    return [
        SignalResponse.from_orm_signal(signal, symbols.get(signal.asset_id, "UNKNOWN"))
        for signal in signals
    ]


@router.get("/signals/{signal_id}", response_model=SignalResponse, summary="Ein Signal im Detail")
async def get_signal(session: SessionDep, signal_id: Annotated[int, Path(ge=1)]) -> SignalResponse:
    signal = await SignalRepository(session).get_by_id(signal_id)
    if signal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Kein Signal mit der ID {signal_id} gefunden.",
        )
    symbols = await AssetRepository(session).get_symbols_by_ids([signal.asset_id])
    return SignalResponse.from_orm_signal(signal, symbols.get(signal.asset_id, "UNKNOWN"))


@router.post(
    "/analysis",
    response_model=AnalysisResponse,
    summary="Ad-hoc-Analyse ausfuehren",
    status_code=status.HTTP_200_OK,
    dependencies=[AdminGuard],
)
async def create_analysis(
    payload: AnalysisRequest,
    service: AnalysisServiceDep,
) -> AnalysisResponse:
    """Analyse fuer ein Symbol berechnen.

    Erzeugt niemals eine Order. Das Ergebnis ist eine Einschaetzung samt
    Begruendung und Risikoparametern.

    Die Datenbank-Session wird bewusst nicht als Dependency deklariert, sondern
    nur bei ``persist=true`` geoeffnet: eine reine Ad-hoc-Analyse soll auch dann
    funktionieren, wenn die Datenbank gerade nicht erreichbar ist.
    """
    set_correlation_id()

    try:
        if payload.persist:
            async with session_scope() as session:
                outcome = await service.analyze(
                    payload.symbol,
                    timeframes=payload.timeframes,
                    session=session,
                    persist=True,
                    use_llm=payload.use_llm,
                )
        else:
            outcome = await service.analyze(
                payload.symbol,
                timeframes=payload.timeframes,
                persist=False,
                use_llm=payload.use_llm,
            )
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AlphaTradeOracleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    return AnalysisResponse(
        signal=SignalResponse.from_result(outcome.result, signal_id=outcome.signal_id),
        skipped_timeframes=outcome.skipped_timeframes,
        llm_used=outcome.llm_analysis is not None,
        llm_status=outcome.llm_call.status if outcome.llm_call else None,
    )


@router.get("/performance", response_model=PerformanceResponse, summary="Auswertung der Signale")
async def performance(
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> PerformanceResponse:
    summary = await SignalRepository(session).performance_summary(days=days)

    by_direction = {
        key.removeprefix("count_").upper(): int(value)
        for key, value in summary.items()
        if key.startswith("count_")
    }

    return PerformanceResponse(
        period_days=int(summary.get("period_days", days)),
        signals_total=int(summary.get("signals_total", 0)),
        signals_dispatched=int(summary.get("signals_dispatched", 0)),
        average_score=float(summary.get("average_score", 0.0)),
        average_risk_reward=float(summary.get("average_risk_reward", 0.0)),
        average_data_quality=float(summary.get("average_data_quality", 0.0)),
        by_direction=by_direction,
    )
