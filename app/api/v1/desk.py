"""Public Alpha Desk snapshot API (paper ledger → dashboard camelCase)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import PaperTradingDep, ProviderDep, SessionDep, UniverseProvidersDep
from app.scheduler.jobs import _collect_prices
from app.schemas.desk import DeskSnapshot
from app.services.desk_service import DeskService

router = APIRouter(prefix="/api/v1/desk", tags=["desk"])


@router.get(
    "/snapshot",
    response_model=DeskSnapshot,
    summary="Alpha Desk Snapshot (Portfolio, Trades, Equity)",
)
async def desk_snapshot(
    session: SessionDep,
    paper: PaperTradingDep,
    provider: ProviderDep,
    providers: UniverseProvidersDep,
) -> DeskSnapshot:
    """Read-only book for the public trading desk.

    Cancelled / retest-skipped rows are omitted — only open, pending, and
    truly closed (exit-filled) trades are returned.
    """
    if paper is None or not paper.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Paper-Trading ist nicht aktiv.",
        )

    account = await paper.get_or_create_account(session)
    from app.repositories.paper_repository import PaperRepository

    open_positions = await PaperRepository(session).list_open_positions(account.id)
    symbols = [p.symbol for p in open_positions]
    prices: dict[str, float] = {}
    if symbols:
        prices = await _collect_prices(provider, symbols, providers=providers)

    return await DeskService(paper).snapshot(session, prices=prices)
