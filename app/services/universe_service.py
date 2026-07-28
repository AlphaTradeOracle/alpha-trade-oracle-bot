"""UniverseService — Market-Cap Top-N von CoinGecko auf Boersen-Paare mappen."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.market_data.base import MarketDataProvider
from app.market_data.coingecko import (
    CoinGeckoClient,
    CoinGeckoMarket,
    CoinGeckoTicker,
    exchange_matches_provider,
)
from app.market_data.factory import parse_universe_exchange_names
from app.market_data.types import SymbolInfo
from app.repositories.asset_repository import AssetRepository

logger = get_logger(__name__)

#: Stablecoins und Pegged Assets haben kein sinnvolles BASE+USDT-Paar fuer Analyse.
SKIP_BASE_ASSETS = frozenset(
    {
        "USDT",
        "USDC",
        "BUSD",
        "DAI",
        "TUSD",
        "FDUSD",
        "USDE",
        "USDD",
        "FRAX",
        "USD1",
        "USDP",
        "GUSD",
        "PYUSD",
        "EUR",
        "EURC",
        "AEUR",
    }
)


@dataclass
class UniverseRefreshResult:
    """Ergebnis eines Universe-Refresh-Laufs."""

    ranked: int = 0
    mapped: int = 0
    mapped_via_direct: int = 0
    mapped_via_ticker: int = 0
    ticker_lookups: int = 0
    skipped_stable: int = 0
    skipped_no_pair: int = 0
    deactivated: int = 0
    symbols: list[str] = field(default_factory=list)

    def as_summary(self) -> dict[str, int]:
        return {
            "ranked": self.ranked,
            "mapped": self.mapped,
            "mapped_via_direct": self.mapped_via_direct,
            "mapped_via_ticker": self.mapped_via_ticker,
            "ticker_lookups": self.ticker_lookups,
            "skipped_stable": self.skipped_stable,
            "skipped_no_pair": self.skipped_no_pair,
            "deactivated": self.deactivated,
        }


class UniverseService:
    """Laedt Top-N Market Cap und schreibt handelbare Paare in ``assets``."""

    def __init__(
        self,
        provider: MarketDataProvider | dict[str, MarketDataProvider],
        coingecko: CoinGeckoClient,
        *,
        settings: Settings | None = None,
    ) -> None:
        if isinstance(provider, dict):
            self._providers = provider
        else:
            self._providers = {provider.name: provider}
        self._coingecko = coingecko
        self._settings = settings or get_settings()
        self._exchange_order = self._resolve_exchange_order()

    def _resolve_exchange_order(self) -> list[str]:
        """Konfigurierte Reihenfolge plus alle vorhandenen Provider-Instanzen."""
        ordered = list(parse_universe_exchange_names(self._settings))
        for name in self._providers:
            if name not in ordered:
                ordered.append(name)
        return ordered

    async def refresh(self, session: AsyncSession) -> UniverseRefreshResult:
        """Universe aus CoinGecko neu aufbauen und mit der Boerse abgleichen."""
        result = UniverseRefreshResult()
        quote = self._settings.default_quote_asset.upper()
        limit = self._settings.universe_size
        ticker_budget = max(0, self._settings.universe_ticker_fallback_max)

        markets = await self._coingecko.fetch_top_markets(limit)
        result.ranked = len(markets)

        exchange_indices = await self._load_exchange_indices(quote)
        assets = AssetRepository(session)
        active_symbols: set[str] = set()

        for market in markets:
            base = market.symbol.upper().strip()
            if not base or base in SKIP_BASE_ASSETS or base == quote:
                result.skipped_stable += 1
                continue

            mapped = self._map_direct(market, quote, exchange_indices)
            via_ticker = False
            if mapped is None and self._settings.universe_ticker_fallback and ticker_budget > 0:
                mapped = await self._map_via_tickers(market, quote, exchange_indices)
                if mapped is not None:
                    via_ticker = True
                    ticker_budget -= 1
                    result.ticker_lookups += 1

            if mapped is None:
                result.skipped_no_pair += 1
                continue

            symbol, info, exchange = mapped
            await assets.upsert_universe_entry(
                symbol=symbol,
                base_asset=info.base_asset,
                quote_asset=info.quote_asset,
                exchange=exchange,
                coingecko_id=market.id,
                market_cap_rank=market.market_cap_rank,
                market_cap_usd=(
                    Decimal(str(market.market_cap)) if market.market_cap is not None else None
                ),
                price_precision=info.price_precision,
                quantity_precision=info.quantity_precision,
                is_active=info.is_active,
            )
            active_symbols.add(symbol)
            result.symbols.append(symbol)
            result.mapped += 1
            if via_ticker:
                result.mapped_via_ticker += 1
            else:
                result.mapped_via_direct += 1

        result.deactivated = await assets.deactivate_stale_universe(active_symbols)

        logger.info(
            "universe_refreshed",
            exchanges=self._exchange_order,
            quote=quote,
            **result.as_summary(),
        )
        return result

    async def _load_exchange_indices(self, quote: str) -> dict[str, dict[str, SymbolInfo]]:
        indices: dict[str, dict[str, SymbolInfo]] = {}
        for exchange in self._exchange_order:
            provider = self._providers.get(exchange)
            if provider is None:
                logger.warning("universe_provider_missing", exchange=exchange)
                continue
            listed = await provider.list_symbols(quote_asset=quote)
            indices[exchange] = {
                info.symbol.upper(): info for info in listed if info.is_active
            }
        return indices

    def _map_direct(
        self,
        market: CoinGeckoMarket,
        quote: str,
        exchange_indices: dict[str, dict[str, SymbolInfo]],
    ) -> tuple[str, SymbolInfo, str] | None:
        base = market.symbol.upper().strip()
        if not base:
            return None
        symbol = f"{base}{quote}"
        for exchange in self._exchange_order:
            info = exchange_indices.get(exchange, {}).get(symbol)
            if info is not None:
                return symbol, info, exchange
        return None

    async def _map_via_tickers(
        self,
        market: CoinGeckoMarket,
        quote: str,
        exchange_indices: dict[str, dict[str, SymbolInfo]],
    ) -> tuple[str, SymbolInfo, str] | None:
        try:
            tickers = await self._coingecko.fetch_coin_tickers(market.id)
        except Exception as exc:
            logger.warning(
                "universe_ticker_lookup_failed",
                coingecko_id=market.id,
                symbol=market.symbol,
                error=str(exc),
            )
            return None

        for exchange in self._exchange_order:
            index = exchange_indices.get(exchange, {})
            match = self._pick_ticker_match(tickers, quote, exchange, index)
            if match is not None:
                return match
        return None

    def _pick_ticker_match(
        self,
        tickers: list[CoinGeckoTicker],
        quote: str,
        exchange: str,
        index: dict[str, SymbolInfo],
    ) -> tuple[str, SymbolInfo, str] | None:
        wanted_quote = quote.upper()
        for ticker in tickers:
            if ticker.target.upper() != wanted_quote:
                continue
            if not exchange_matches_provider(ticker, exchange):
                continue
            base = ticker.base.upper().strip()
            if not base or base in SKIP_BASE_ASSETS or base == wanted_quote:
                continue
            symbol = f"{base}{wanted_quote}"
            info = index.get(symbol)
            if info is not None:
                return symbol, info, exchange
        return None
