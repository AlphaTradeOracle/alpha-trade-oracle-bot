"""Public Alpha Desk snapshot API (paper ledger → dashboard camelCase)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import PaperTradingDep, ProviderDep, SessionDep, UniverseProvidersDep
from app.core.errors import MarketDataError, SymbolNotFoundError
from app.core.logging import get_logger
from app.core.time import ms_to_datetime
from app.market_data.base import MarketDataProvider
from app.repositories.paper_repository import PaperRepository
from app.scheduler.jobs import _collect_prices
from app.schemas.desk import DeskCandle, DeskSnapshot
from app.services.desk_service import DeskService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/desk", tags=["desk"])

_ALLOWED_INTERVALS = frozenset(
    {"1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "3d", "1w"}
)


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
    if not paper.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Paper-Trading ist nicht aktiv.",
        )

    account = await paper.get_or_create_account(session)
    open_positions = await PaperRepository(session).list_open_positions(account.id)
    symbols = [p.symbol for p in open_positions]
    prices: dict[str, float] = {}
    if symbols:
        prices = await _collect_prices(provider, symbols, providers=providers)

    return await DeskService(paper).snapshot(session, prices=prices)


@router.get(
    "/candles",
    response_model=list[DeskCandle],
    summary="OHLCV candles for desk trade charts",
)
async def desk_candles(
    provider: ProviderDep,
    providers: UniverseProvidersDep,
    symbol: Annotated[str, Query(min_length=3, max_length=32)],
    interval: Annotated[str, Query(min_length=2, max_length=8)] = "1h",
    from_ts: Annotated[int, Query(alias="from", ge=0)] = 0,
    to_ts: Annotated[int | None, Query(alias="to", ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=1500)] = 1000,
) -> list[DeskCandle]:
    """Proxy exchange candles so the public desk avoids browser CORS limits."""
    tf = interval.strip().lower()
    if tf not in _ALLOWED_INTERVALS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported interval '{interval}'.",
        )

    sym = symbol.strip().upper()
    start = ms_to_datetime(from_ts * 1000) if from_ts > 0 else None
    end = ms_to_datetime(to_ts * 1000) if to_ts else None

    series = await _fetch_candles_any(
        provider,
        providers,
        symbol=sym,
        timeframe=tf,
        start_time=start,
        end_time=end,
        limit=limit,
    )
    out: list[DeskCandle] = []
    for candle in series.candles:
        open_time = candle.open_time
        if open_time.tzinfo is None:
            open_time = open_time.replace(tzinfo=UTC)
        unix = int(open_time.timestamp())
        if from_ts and unix < from_ts:
            continue
        if to_ts is not None and unix > to_ts:
            continue
        out.append(
            DeskCandle(
                time=unix,
                open=float(candle.open),
                high=float(candle.high),
                low=float(candle.low),
                close=float(candle.close),
                volume=float(candle.volume),
            )
        )
    return out


async def _fetch_candles_any(
    primary: MarketDataProvider,
    providers: dict[str, MarketDataProvider] | None,
    *,
    symbol: str,
    timeframe: str,
    start_time: datetime | None,
    end_time: datetime | None,
    limit: int,
):
    candidates: list[MarketDataProvider] = [primary]
    for candidate in (providers or {}).values():
        if candidate is not primary and candidate not in candidates:
            candidates.append(candidate)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            series = await candidate.get_candles(
                symbol,
                timeframe,
                limit=limit,
                start_time=start_time,
                end_time=end_time,
                include_unclosed=True,
            )
            if series.candles:
                return series
        except SymbolNotFoundError as exc:
            last_error = exc
            continue
        except MarketDataError as exc:
            last_error = exc
            logger.warning(
                "desk_candles_provider_failed",
                provider=getattr(candidate, "name", "?"),
                symbol=symbol,
                error=str(exc),
            )
            continue
        except Exception as exc:  # noqa: BLE001 — try next venue
            last_error = exc
            logger.warning(
                "desk_candles_provider_failed",
                provider=getattr(candidate, "name", "?"),
                symbol=symbol,
                error=str(exc),
            )
            continue

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Keine Kerzen fuer {symbol} ({timeframe}): {last_error}",
    )
