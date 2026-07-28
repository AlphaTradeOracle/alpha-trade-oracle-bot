"""Provider-Auswahl anhand der Konfiguration."""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError
from app.core.logging import get_logger
from app.market_data.base import MarketDataProvider
from app.market_data.binance import BinanceMarketDataProvider
from app.market_data.cache import CachedMarketDataProvider
from app.market_data.kucoin import KucoinMarketDataProvider

logger = get_logger(__name__)

#: Registrierte Provider. Weitere Boersen werden hier ergaenzt, ohne dass
#: aufrufender Code angepasst werden muss.
_PROVIDERS: dict[str, Callable[..., MarketDataProvider]] = {
    "binance": BinanceMarketDataProvider,
    "kucoin": KucoinMarketDataProvider,
}


def create_market_data_provider(
    settings: Settings | None = None, *, redis_client: object | None = None
) -> MarketDataProvider:
    """Provider gemaess ``MARKET_DATA_PROVIDER`` erzeugen, optional mit Cache."""
    cfg = settings or get_settings()
    key = cfg.market_data_provider.lower().strip()

    provider_class = _PROVIDERS.get(key)
    if provider_class is None:
        raise ConfigurationError(
            f"Unbekannter Marktdaten-Provider: {cfg.market_data_provider!r}.",
            detail=f"Verfuegbar: {', '.join(sorted(_PROVIDERS))}",
        )

    provider: MarketDataProvider = provider_class(cfg)

    if redis_client is not None and cfg.market_data_cache_ttl_seconds > 0:
        provider = CachedMarketDataProvider(provider, redis_client, cfg)  # type: ignore[assignment]
        logger.debug("market_data_cache_enabled", ttl=cfg.market_data_cache_ttl_seconds)

    return provider


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)


def parse_universe_exchange_names(settings: Settings | None = None) -> list[str]:
    """Boersen fuer das Universe-Mapping in Prioritaetsreihenfolge."""
    cfg = settings or get_settings()
    primary = cfg.market_data_provider.lower().strip()
    configured = [name.strip().lower() for name in cfg.universe_exchanges.split(",") if name.strip()]
    ordered: list[str] = []

    if primary:
        ordered.append(primary)
    for name in configured:
        if name in _PROVIDERS and name not in ordered:
            ordered.append(name)
    if not ordered and primary in _PROVIDERS:
        ordered = [primary]
    return ordered


def create_universe_providers(
    settings: Settings | None = None, *, redis_client: object | None = None
) -> dict[str, MarketDataProvider]:
    """Provider-Instanzen fuer alle im Universe konfigurierten Boersen."""
    cfg = settings or get_settings()
    providers: dict[str, MarketDataProvider] = {}
    for name in parse_universe_exchange_names(cfg):
        provider: MarketDataProvider = _PROVIDERS[name](cfg)
        if redis_client is not None and cfg.market_data_cache_ttl_seconds > 0:
            provider = CachedMarketDataProvider(provider, redis_client, cfg)  # type: ignore[assignment]
        providers[name] = provider
    return providers
