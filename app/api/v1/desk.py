"""Public Alpha Desk snapshot API (paper ledger → dashboard camelCase)."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import (
    PaperPriceProviderDep,
    PaperTradingDep,
    ProviderDep,
    SessionDep,
    UniverseProvidersDep,
)
from app.core.config import get_settings
from app.core.errors import MarketDataError, SymbolNotFoundError
from app.core.logging import get_logger
from app.core.time import ms_to_datetime, utc_now
from app.market_data.base import MarketDataProvider
from app.market_data.coingecko import CoinGeckoClient
from app.market_regime import MarketRegimeEngine
from app.repositories.paper_repository import PaperRepository
from app.scheduler.jobs import _collect_prices
from app.schemas.desk import (
    DeskCandle,
    DeskMarketRegime,
    DeskSnapshot,
    DeskTopCoin,
    DeskTopCoinsResponse,
)
from app.services.desk_service import DeskService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/desk", tags=["desk"])

_ALLOWED_INTERVALS = frozenset(
    {"1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "3d", "1w"}
)

# Process-local regime cache — full resolve hits many external venues (~5–8s).
_REGIME_TTL_SECONDS = 90.0
_regime_cache: DeskMarketRegime | None = None
_regime_cache_at: float = 0.0
_regime_lock = asyncio.Lock()

# Top-coins banner cache (CoinGecko markets + sparkline).
_TOP_COINS_TTL_SECONDS = 60.0
_top_coins_cache: DeskTopCoinsResponse | None = None
_top_coins_cache_at: float = 0.0
_top_coins_lock = asyncio.Lock()


async def _cached_desk_regime(provider: MarketDataProvider) -> DeskMarketRegime | None:
    """Return market regime, refreshing at most once per TTL window."""
    global _regime_cache, _regime_cache_at

    settings = get_settings()
    if not settings.market_regime_enabled:
        return None

    now = time.monotonic()
    if _regime_cache is not None and (now - _regime_cache_at) < _REGIME_TTL_SECONDS:
        return _regime_cache

    async with _regime_lock:
        now = time.monotonic()
        if _regime_cache is not None and (now - _regime_cache_at) < _REGIME_TTL_SECONDS:
            return _regime_cache

        engine = MarketRegimeEngine(settings)
        try:
            snap = await engine.resolve(provider, refresh=True)
            payload = snap.to_desk_dict()
            payload["hardVeto"] = bool(settings.market_regime_hard_veto)
            payload["scoreBlend"] = True
            regime = DeskMarketRegime.model_validate(payload)
            _regime_cache = regime
            _regime_cache_at = time.monotonic()
            return regime
        except Exception as exc:  # noqa: BLE001
            logger.warning("desk_market_regime_failed", error=str(exc))
            return _regime_cache
        finally:
            await engine.close()


@router.get(
    "/snapshot",
    response_model=DeskSnapshot,
    summary="Alpha Desk Snapshot (Portfolio, Trades, Equity)",
)
async def desk_snapshot(
    session: SessionDep,
    paper: PaperTradingDep,
    provider: ProviderDep,
    price_provider: PaperPriceProviderDep,
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

    async def _prices() -> dict[str, float]:
        if not symbols:
            return {}
        # Open marks from the same perp venues used for paper fills.
        return await _collect_prices(price_provider, symbols, providers=None)

    prices, market_regime = await asyncio.gather(
        _prices(),
        _cached_desk_regime(provider),
    )

    return await DeskService(paper).snapshot(
        session, prices=prices, market_regime=market_regime
    )


@router.get(
    "/top-coins",
    response_model=DeskTopCoinsResponse,
    summary="Top market-cap coins with live price and 7d sparkline",
)
async def desk_top_coins(
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> DeskTopCoinsResponse:
    """Public banner feed — CoinGecko top markets, cached ~60s."""
    global _top_coins_cache, _top_coins_cache_at

    now = time.monotonic()
    if (
        _top_coins_cache is not None
        and (now - _top_coins_cache_at) < _TOP_COINS_TTL_SECONDS
        and len(_top_coins_cache.coins) >= limit
    ):
        if len(_top_coins_cache.coins) == limit:
            return _top_coins_cache
        return DeskTopCoinsResponse(
            coins=_top_coins_cache.coins[:limit],
            generatedAt=_top_coins_cache.generatedAt,
            source=_top_coins_cache.source,
        )

    async with _top_coins_lock:
        now = time.monotonic()
        if (
            _top_coins_cache is not None
            and (now - _top_coins_cache_at) < _TOP_COINS_TTL_SECONDS
            and len(_top_coins_cache.coins) >= limit
        ):
            if len(_top_coins_cache.coins) == limit:
                return _top_coins_cache
            return DeskTopCoinsResponse(
                coins=_top_coins_cache.coins[:limit],
                generatedAt=_top_coins_cache.generatedAt,
                source=_top_coins_cache.source,
            )

        client = CoinGeckoClient(get_settings())
        try:
            markets = await client.fetch_live_markets(limit)
        except MarketDataError as exc:
            logger.warning("desk_top_coins_failed", error=str(exc))
            if _top_coins_cache is not None:
                return _top_coins_cache
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Top-Coins Feed nicht erreichbar: {exc}",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.warning("desk_top_coins_failed", error=str(exc))
            if _top_coins_cache is not None:
                return _top_coins_cache
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Top-Coins Feed fehlgeschlagen.",
            ) from exc
        finally:
            await client.close()

        coins = [
            DeskTopCoin(
                id=m.id,
                symbol=m.symbol,
                name=m.name,
                rank=m.market_cap_rank,
                priceUsd=m.price_usd,
                change24hPct=m.change_24h_pct,
                marketCapUsd=m.market_cap_usd,
                volume24hUsd=m.volume_24h_usd,
                circulatingSupply=m.circulating_supply,
                imageUrl=m.image_url,
                sparkline=list(m.sparkline),
            )
            for m in markets
        ]
        payload = DeskTopCoinsResponse(
            coins=coins,
            generatedAt=utc_now().isoformat().replace("+00:00", "Z"),
            source="coingecko",
        )
        _top_coins_cache = payload
        _top_coins_cache_at = time.monotonic()
        return payload


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
