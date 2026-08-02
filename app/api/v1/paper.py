"""Endpunkte fuer Paper-Trading."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminGuard, PaperTradingDep, ProviderDep, SessionDep, UniverseProvidersDep
from app.repositories.paper_repository import PaperRepository
from app.scheduler.jobs import _collect_prices
from app.schemas.paper import PaperPositionResponse, PaperSummaryResponse, PaperUpdateResponse

router = APIRouter(prefix="/api/v1/paper", tags=["paper"])


@router.get(
    "/summary",
    response_model=PaperSummaryResponse,
    summary="Paper-Depot Zusammenfassung",
    dependencies=[AdminGuard],
)
async def paper_summary(session: SessionDep, paper: PaperTradingDep) -> PaperSummaryResponse:
    summary = await paper.summary(session)
    return PaperSummaryResponse(
        cash_balance=summary.cash_balance,
        initial_balance=summary.initial_balance,
        realized_pnl=summary.realized_pnl,
        open_positions=summary.open_positions,
        open_margin=summary.open_margin,
        equity=summary.equity,
        win_rate=summary.win_rate,
        closed_trades=summary.closed_trades,
        profit_factor=summary.profit_factor,
        total_r=summary.total_r,
        expectancy_r=summary.expectancy_r,
        fees_r=summary.fees_r,
        r_trades=summary.r_trades,
    )


@router.get(
    "/positions",
    response_model=list[PaperPositionResponse],
    summary="Paper-Positionen",
    dependencies=[AdminGuard],
)
async def paper_positions(
    session: SessionDep,
    paper: PaperTradingDep,
    status_filter: Annotated[str, Query(alias="status")] = "open",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[PaperPositionResponse]:
    account = await paper.get_or_create_account(session)
    repo = PaperRepository(session)
    if status_filter == "closed":
        positions = await repo.list_closed(account.id, limit=limit)
    else:
        positions = await repo.list_open_positions(account.id)
        positions = positions[:limit]
    return [
        PaperPositionResponse(
            id=p.id,
            symbol=p.symbol,
            direction=p.direction,
            status=p.status,
            timeframe=p.timeframe,
            entry_price=float(p.entry_price),
            stop_loss=float(p.stop_loss),
            current_stop=float(p.current_stop),
            take_profit_1=float(p.take_profit_1),
            take_profit_2=float(p.take_profit_2),
            take_profit_3=float(p.take_profit_3),
            initial_quantity=float(p.initial_quantity),
            remaining_quantity=float(p.remaining_quantity),
            margin_used=float(p.margin_used),
            notional=float(p.notional),
            leverage=float(p.leverage),
            tp1_filled=p.tp1_filled,
            tp2_filled=p.tp2_filled,
            tp3_filled=p.tp3_filled,
            realized_pnl=float(p.realized_pnl),
            fees=float(p.fees),
            risk_amount=float(p.risk_amount),
            r_multiple=(
                float(p.realized_pnl) / float(p.risk_amount)
                if float(p.risk_amount) > 0
                else None
            ),
            signal_score=float(p.signal_score) if p.signal_score is not None else None,
            exit_reason=p.exit_reason,
            opened_at=p.opened_at,
            closed_at=p.closed_at,
        )
        for p in positions
    ]


@router.post(
    "/update",
    response_model=PaperUpdateResponse,
    summary="Offene Paper-Positionen gegen Kurse aktualisieren",
    dependencies=[AdminGuard],
)
async def paper_update(
    session: SessionDep,
    paper: PaperTradingDep,
    provider: ProviderDep,
    providers: UniverseProvidersDep,
) -> PaperUpdateResponse:
    account = await paper.get_or_create_account(session)
    open_positions = await PaperRepository(session).list_open_positions(account.id)
    symbols = [p.symbol for p in open_positions]
    if not symbols:
        return PaperUpdateResponse(updated=0, prices=0, open_positions=0)

    prices = await _collect_prices(provider, symbols, providers=providers)
    updated = await paper.update_open_positions(
        session, prices, provider=provider, wick_timeframe="5m"
    )
    return PaperUpdateResponse(
        updated=len(updated),
        prices=len(prices),
        open_positions=len(open_positions),
    )
