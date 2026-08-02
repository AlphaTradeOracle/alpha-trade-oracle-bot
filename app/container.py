"""Aufbau und Abbau der Anwendungsabhaengigkeiten.

Bewusst als schlanke Fabrik statt DI-Framework: die Abhaengigkeiten sind
ueberschaubar, und ein explizites Aufbaudiagramm ist leichter zu pruefen als
eine implizite Auflösung zur Laufzeit.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.database.redis_client import close_redis, get_redis
from app.database.session import dispose_engine
from app.llm.factory import create_llm_service
from app.llm.service import LLMService
from app.market_data.base import MarketDataProvider
from app.market_data.coingecko import CoinGeckoClient
from app.market_data.factory import create_market_data_provider, create_universe_providers
from app.monitoring.health import HealthService
from app.sentiment.service import SentimentService
from app.services.analysis_service import AnalysisService
from app.services.backtest_service import BacktestService
from app.services.data_retention_service import DataRetentionService
from app.services.paper_trading_service import PaperTradingService
from app.services.scan_service import ScanService
from app.services.universe_service import UniverseService
from app.signals.dedup import SignalDeduplicator

logger = get_logger(__name__)


@dataclass
class ApplicationContainer:
    """Alle langlebigen Objekte der Anwendung."""

    settings: Settings
    provider: MarketDataProvider
    universe_providers: dict[str, MarketDataProvider]
    coingecko: CoinGeckoClient
    llm_service: LLMService
    sentiment_service: SentimentService
    analysis_service: AnalysisService
    backtest_service: BacktestService
    universe_service: UniverseService
    data_retention: DataRetentionService
    paper_trading: PaperTradingService
    deduplicator: SignalDeduplicator
    health_service: HealthService
    scan_service: ScanService | None = None

    async def aclose(self) -> None:
        """Alle Ressourcen freigeben. Einzelne Fehler stoppen den Abbau nicht."""
        closed: set[int] = set()
        for name, closer in (
            ("coingecko", self.coingecko.close),
            ("sentiment_service", self.sentiment_service.close),
            ("market_regime", self.analysis_service._regime_engine.close),
            ("redis", close_redis),
            ("database", dispose_engine),
        ):
            try:
                await closer()
            except Exception as exc:
                logger.warning("shutdown_step_failed", component=name, error=str(exc))

        for exchange, provider in self.universe_providers.items():
            if id(provider) in closed:
                continue
            try:
                await provider.close()
                closed.add(id(provider))
            except Exception as exc:
                logger.warning(
                    "shutdown_step_failed",
                    component=f"market_data_provider:{exchange}",
                    error=str(exc),
                )


def build_container(settings: Settings | None = None) -> ApplicationContainer:
    """Container aufbauen. Redis-Ausfaelle werden erst beim Zugriff sichtbar."""
    cfg = settings or get_settings()

    redis_client = get_redis(cfg)
    universe_providers = create_universe_providers(cfg, redis_client=redis_client)
    primary_key = cfg.market_data_provider.lower().strip()
    provider = universe_providers.get(primary_key)
    if provider is None:
        provider = create_market_data_provider(cfg, redis_client=redis_client)
        universe_providers[primary_key] = provider
    coingecko = CoinGeckoClient(cfg)
    llm_service = create_llm_service(cfg)
    sentiment_service = SentimentService(settings=cfg)

    analysis_service = AnalysisService(
        provider,
        providers=universe_providers,
        settings=cfg,
        llm_service=llm_service,
        sentiment_service=sentiment_service,
    )
    backtest_service = BacktestService(provider, settings=cfg)
    universe_service = UniverseService(universe_providers, coingecko, settings=cfg)
    data_retention = DataRetentionService(universe_providers, settings=cfg)
    paper_trading = PaperTradingService(settings=cfg)

    deduplicator = SignalDeduplicator(
        cooldown_minutes=cfg.signal_cooldown_minutes, redis_client=redis_client
    )

    health_service = HealthService(
        cfg,
        market_data_check=provider.health_check,
        llm_check=_llm_health_check(llm_service),
    )

    logger.info(
        "container_built",
        provider=provider.name,
        llm_enabled=llm_service.is_enabled,
        sentiment_enabled=cfg.enable_sentiment,
        universe_scan=cfg.enable_universe_scan,
        paper_trading=cfg.enable_paper_trading,
    )

    return ApplicationContainer(
        settings=cfg,
        provider=provider,
        universe_providers=universe_providers,
        coingecko=coingecko,
        llm_service=llm_service,
        sentiment_service=sentiment_service,
        analysis_service=analysis_service,
        backtest_service=backtest_service,
        universe_service=universe_service,
        data_retention=data_retention,
        paper_trading=paper_trading,
        deduplicator=deduplicator,
        health_service=health_service,
    )


def _llm_health_check(llm_service: LLMService):  # type: ignore[no-untyped-def]
    """Healthcheck des LLM-Providers, sofern einer konfiguriert ist."""
    provider = getattr(llm_service, "_provider", None)
    if provider is None:
        return None
    return provider.health_check
