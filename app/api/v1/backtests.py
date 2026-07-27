"""Backtest-Endpunkte. Das Anlegen ist administrativ geschuetzt."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from app.api.deps import AdminGuard, BacktestServiceDep, SessionDep
from app.core.errors import AlphaTradeOracleError
from app.core.logging import get_logger, set_correlation_id
from app.repositories.backtest_repository import BacktestRepository
from app.schemas.backtest import BacktestRequest, BacktestResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["backtests"])


@router.post(
    "/backtests",
    response_model=BacktestResponse,
    summary="Backtest ausfuehren (administrativ)",
    dependencies=[AdminGuard],
)
async def create_backtest(
    payload: BacktestRequest,
    session: SessionDep,
    service: BacktestServiceDep,
) -> BacktestResponse:
    """Backtest synchron ausfuehren.

    Der Aufruf ist bewusst administrativ geschuetzt: ein Backtest ueber einen
    langen Zeitraum belastet die Marktdaten-API erheblich.
    """
    set_correlation_id()

    try:
        report = await service.run(
            payload.symbol,
            payload.timeframe,
            payload.start,
            payload.end,
            session=session,
            fee_percent=payload.fee_percent,
            slippage_percent=payload.slippage_percent,
            initial_capital=payload.initial_capital,
        )
    except AlphaTradeOracleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    closed_trades = [trade for trade in report.outcome.trades if trade.is_closed]

    return BacktestResponse(
        run_id=report.run_id,
        symbol=payload.symbol.upper(),
        timeframe=payload.timeframe,
        start=payload.start,
        end=payload.end,
        status="completed",
        candles_loaded=report.candles_loaded,
        trade_count=len(closed_trades),
        metrics=report.metrics,
    )


@router.get(
    "/backtests/{backtest_id}",
    response_model=BacktestResponse,
    summary="Ergebnis eines Backtests",
)
async def get_backtest(
    session: SessionDep, backtest_id: Annotated[int, Path(ge=1)]
) -> BacktestResponse:
    repository = BacktestRepository(session)
    run = await repository.get_run(backtest_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Kein Backtest mit der ID {backtest_id} gefunden.",
        )

    metrics = await repository.get_metrics(backtest_id)
    trade_count = int(metrics.get("overall", {}).get("trade_count", 0))

    return BacktestResponse(
        run_id=run.id,
        symbol=run.symbol,
        timeframe=run.timeframe,
        start=run.start_at,
        end=run.end_at,
        status=run.status,
        candles_loaded=0,
        trade_count=trade_count,
        metrics=metrics,
    )
