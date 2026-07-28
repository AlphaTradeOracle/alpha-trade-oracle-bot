"""UniverseService — Market-Cap Top-N von CoinGecko auf Boersen-Paare mappen."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.market_data.base import MarketDataProvider
from app.market_data.coingecko import CoinGeckoClient, CoinGeckoMarket
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
    skipped_stable: int = 0
    skipped_no_pair: int = 0
    deactivated: int = 0
    symbols: list[str] = field(default_factory=list)

    def as_summary(self) -> dict[str, int]:
        return {
            "ranked": self.ranked,
            "mapped": self.mapped,
            "skipped_stable": self.skipped_stable,
            "skipped_no_pair": self.skipped_no_pair,
            "deactivated": self.deactivated,
        }


class UniverseService:
    """Laedt Top-N Market Cap und schreibt handelbare Paare in ``assets``."""

    def __init__(
        self,
        provider: MarketDataProvider,
        coingecko: CoinGeckoClient,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._provider = provider
        self._coingecko = coingecko
        self._settings = settings or get_settings()

    async def refresh(self, session: AsyncSession) -> UniverseRefreshResult:
        """Universe aus CoinGecko neu aufbauen und mit der Boerse abgleichen."""
        result = UniverseRefreshResult()
        quote = self._settings.default_quote_asset.upper()
        limit = self._settings.universe_size

        markets = await self._coingecko.fetch_top_markets(limit)
        result.ranked = len(markets)

        exchange_symbols = await self._load_exchange_index(quote)
        assets = AssetRepository(session)
        active_symbols: set[str] = set()

        for market in markets:
            mapped = self._map_market(market, quote, exchange_symbols)
            if mapped is None:
                if market.symbol in SKIP_BASE_ASSETS or market.symbol == quote:
                    result.skipped_stable += 1
                else:
                    result.skipped_no_pair += 1
                continue

            symbol, info = mapped
            await assets.upsert_universe_entry(
                symbol=symbol,
                base_asset=info.base_asset,
                quote_asset=info.quote_asset,
                exchange=self._provider.name,
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

        result.deactivated = await assets.deactivate_stale_universe(active_symbols)

        logger.info(
            "universe_refreshed",
            exchange=self._provider.name,
            quote=quote,
            **result.as_summary(),
        )
        return result

    async def _load_exchange_index(self, quote: str) -> dict[str, SymbolInfo]:
        listed = await self._provider.list_symbols(quote_asset=quote)
        return {info.symbol.upper(): info for info in listed if info.is_active}

    def _map_market(
        self,
        market: CoinGeckoMarket,
        quote: str,
        exchange_symbols: dict[str, SymbolInfo],
    ) -> tuple[str, SymbolInfo] | None:
        base = market.symbol.upper().strip()
        if not base or base in SKIP_BASE_ASSETS or base == quote:
            return None
        symbol = f"{base}{quote}"
        info = exchange_symbols.get(symbol)
        if info is None:
            return None
        return symbol, info
