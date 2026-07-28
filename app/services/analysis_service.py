"""AnalysisService — orchestriert den vertikalen Ablauf einer Analyse.

Ablauf: Marktdaten laden, Indikatoren berechnen, Signal erzeugen, Risiko
bestimmen, optional durch das LLM zusammenfassen und persistieren.

Der Service enthaelt selbst keine Fachlogik. Er verbindet die Bausteine und
sorgt dafuer, dass ein Ausfall optionaler Komponenten (LLM, Redis) den Ablauf
nicht abbricht.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import InsufficientDataError, MarketDataError
from app.core.logging import get_logger
from app.indicators.engine import IndicatorEngine, IndicatorSet
from app.llm.schemas import LLMAnalysisResponse, LLMCallResult
from app.llm.service import LLMService
from app.market_data.base import MarketDataProvider
from app.market_data.types import CandleSeries
from app.repositories.asset_repository import AssetRepository
from app.repositories.signal_repository import SignalRepository
from app.repositories.strategy_repository import StrategyRepository
from app.sentiment.service import SentimentService
from app.signals.engine import SignalEngine, signal_engine_config_from_settings
from app.signals.risk import RiskConfig, RiskManager
from app.signals.types import SignalResult
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

logger = get_logger(__name__)


@dataclass
class AnalysisOutcome:
    """Ergebnis einer Analyse samt Kontext fuer Darstellung und Persistierung."""

    result: SignalResult
    price_precision: int
    signal_id: int | None = None
    asset_id: int | None = None
    llm_analysis: LLMAnalysisResponse | None = None
    llm_call: LLMCallResult | None = None
    chart_series: CandleSeries | None = None
    #: Timeframes, die wegen Datenmangel oder API-Fehler ausgelassen wurden.
    skipped_timeframes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.skipped_timeframes is None:
            self.skipped_timeframes = []


class AnalysisService:
    """Erzeugt eine vollstaendige Analyse fuer ein Symbol."""

    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        settings: Settings | None = None,
        llm_service: LLMService | None = None,
        sentiment_service: SentimentService | None = None,
        indicator_engine: IndicatorEngine | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = provider
        self._llm = llm_service
        self._sentiment = sentiment_service
        self._indicators = indicator_engine or IndicatorEngine(
            min_candles=self._settings.min_candles_required
        )

    @property
    def provider(self) -> MarketDataProvider:
        """Der genutzte Marktdaten-Provider, z. B. fuer Statusabfragen."""
        return self._provider

    async def analyze(
        self,
        symbol: str,
        *,
        timeframes: list[str] | None = None,
        session: AsyncSession | None = None,
        weights: StrategyWeights | None = None,
        strategy_version_id: int | None = None,
        persist: bool = True,
        use_llm: bool | None = None,
    ) -> AnalysisOutcome:
        """Vollstaendige Analyse durchfuehren.

        Args:
            symbol: Handelspaar, z. B. ``BTCUSDT``.
            timeframes: Abweichende Timeframes; sonst die Konfiguration.
            session: DB-Session. Ohne Session wird nicht persistiert.
            weights: Abweichende Gewichtung, sonst die aktive Strategieversion.
            persist: Steuert die Persistierung unabhaengig von der Session.
            use_llm: Ueberschreibt ``ENABLE_LLM_ANALYSIS`` fuer diesen Aufruf.
        """
        normalized = symbol.upper().strip()
        target_timeframes = timeframes or self._settings.timeframes

        info = await self._provider.get_symbol_info(normalized)
        if not info.is_active:
            raise MarketDataError(
                f"Handelspaar {normalized} wird derzeit nicht gehandelt.",
                detail=f"Status beim Provider {self._provider.name}: inaktiv",
            )

        series_map = await self._provider.get_multi_timeframe_candles(
            normalized, target_timeframes, limit=self._settings.candle_limit
        )
        if not series_map:
            raise MarketDataError(
                f"Fuer {normalized} konnten keine Marktdaten geladen werden.",
                detail=f"Timeframes: {', '.join(target_timeframes)}",
            )

        indicator_sets, skipped, data_quality = self._compute_indicators(
            normalized, series_map, target_timeframes
        )
        if not indicator_sets:
            raise InsufficientDataError(
                normalized,
                ",".join(target_timeframes),
                0,
                self._settings.min_candles_required,
            )

        # Gewichtung: Vorgabe > aktive Strategieversion > Standard.
        effective_weights = weights
        version_id = strategy_version_id
        if effective_weights is None and session is not None:
            effective_weights, version_id = await StrategyRepository(session).load_weights()
        if effective_weights is None:
            effective_weights = DEFAULT_WEIGHTS

        engine = self._build_engine(effective_weights)
        sentiment_score = await self._load_sentiment(normalized)

        result = engine.generate(
            normalized,
            indicator_sets,
            data_quality=data_quality,
            sentiment_score=sentiment_score,
        )

        logger.info(
            "analysis_completed",
            symbol=normalized,
            direction=result.direction.value,
            score=result.score,
            confidence=result.confidence.value,
            data_quality=result.data_quality,
            timeframes=result.analyzed_timeframes,
            skipped_timeframes=skipped,
        )

        outcome = AnalysisOutcome(
            result=result,
            price_precision=info.price_precision,
            skipped_timeframes=skipped,
            chart_series=self._select_chart_series(series_map, result.primary_timeframe),
        )

        await self._attach_llm_summary(outcome, use_llm=use_llm)

        if persist and session is not None:
            await self._persist(session, outcome, info, indicator_sets, series_map, version_id)

        return outcome

    def _select_chart_series(
        self, series_map: dict[str, CandleSeries], primary_timeframe: str
    ) -> CandleSeries | None:
        """Kerzen fuer das Telegram-Chart (primaerer Timeframe, sonst Fallback)."""
        preferred = series_map.get(primary_timeframe)
        if preferred is not None and not preferred.is_empty:
            return preferred
        for timeframe in self._settings.timeframes:
            series = series_map.get(timeframe)
            if series is not None and not series.is_empty:
                return series
        for series in series_map.values():
            if not series.is_empty:
                return series
        return None

    # --- Teilschritte -----------------------------------------------------

    def _compute_indicators(
        self,
        symbol: str,
        series_map: dict[str, CandleSeries],
        requested: list[str],
    ) -> tuple[dict[str, IndicatorSet], list[str], float]:
        """Indikatoren je Timeframe berechnen; unbrauchbare Timeframes auslassen."""
        indicator_sets: dict[str, IndicatorSet] = {}
        skipped: list[str] = []
        qualities: list[float] = []

        for timeframe in requested:
            series = series_map.get(timeframe)
            if series is None or series.is_empty:
                skipped.append(timeframe)
                continue
            try:
                indicator_sets[timeframe] = self._indicators.compute(
                    series.to_dataframe(), timeframe, symbol=symbol
                )
            except InsufficientDataError as exc:
                # Ein zu kurzer Timeframe darf die Gesamtanalyse nicht verhindern.
                logger.warning(
                    "timeframe_skipped_insufficient_data",
                    symbol=symbol,
                    timeframe=timeframe,
                    available=exc.available,
                    required=exc.required,
                )
                skipped.append(timeframe)
                continue
            qualities.append(series.data_quality(min_candles=self._settings.min_candles_required))

        base_quality = sum(qualities) / len(qualities) if qualities else 0.0
        # Fehlende Timeframes senken die Datenqualitaet proportional.
        coverage = len(indicator_sets) / len(requested) if requested else 0.0
        data_quality = round(base_quality * coverage, 2)

        return indicator_sets, skipped, data_quality

    def _build_engine(self, weights: StrategyWeights) -> SignalEngine:
        risk_manager = RiskManager(
            RiskConfig(
                atr_multiplier=self._settings.atr_multiplier,
                min_risk_reward_ratio=self._settings.min_risk_reward_ratio,
                max_risk_percent=self._settings.max_risk_percent,
                min_stop_distance_percent=self._settings.min_stop_distance_percent,
                max_stop_distance_percent=self._settings.max_stop_distance_percent,
                reference_capital=self._settings.reference_capital,
            )
        )
        config = signal_engine_config_from_settings(
            self._settings,
            weights=weights,
            enable_sentiment=self._settings.enable_sentiment,
        )
        return SignalEngine(config, risk_manager)

    async def _load_sentiment(self, symbol: str) -> float | None:
        """Sentiment laden. Ohne verlaessliche Daten wird kein Wert erfunden."""
        if not self._settings.enable_sentiment or self._sentiment is None:
            return None
        try:
            return await self._sentiment.get_score(symbol)
        except Exception as exc:
            logger.warning("sentiment_unavailable", symbol=symbol, error=str(exc))
            return None

    async def _attach_llm_summary(self, outcome: AnalysisOutcome, *, use_llm: bool | None) -> None:
        """LLM-Zusammenfassung ergaenzen. Bei jedem Fehler bleibt der Text regelbasiert."""
        enabled = self._settings.enable_llm_analysis if use_llm is None else use_llm
        if not enabled or self._llm is None or not self._llm.is_enabled:
            return

        call = await self._llm.summarize(outcome.result)
        outcome.llm_call = call
        if call.analysis is not None:
            outcome.llm_analysis = call.analysis
            outcome.result.llm_summary = call.analysis.summary
        else:
            logger.info(
                "llm_fallback_to_rule_based",
                symbol=outcome.result.symbol,
                status=call.status,
                validation_error=call.validation_error,
            )

    async def _persist(
        self,
        session: AsyncSession,
        outcome: AnalysisOutcome,
        info: object,
        indicator_sets: dict[str, IndicatorSet],
        series_map: dict[str, CandleSeries],
        strategy_version_id: int | None,
    ) -> None:
        assets = AssetRepository(session)
        signals = SignalRepository(session)

        asset = await assets.get_or_create(info, exchange=self._provider.name)  # type: ignore[arg-type]
        outcome.asset_id = asset.id

        for timeframe, series in series_map.items():
            if timeframe in indicator_sets:
                await assets.upsert_candles(asset.id, series)
        for indicators in indicator_sets.values():
            await assets.save_indicator_snapshot(asset.id, indicators)

        signal = await signals.create(
            outcome.result,
            asset.id,
            strategy_version_id=strategy_version_id,
            llm_summary=outcome.result.llm_summary,
        )
        outcome.signal_id = signal.id

        if outcome.llm_call is not None:
            await signals.record_llm_request(outcome.llm_call, signal_id=signal.id)

        logger.debug("analysis_persisted", symbol=outcome.result.symbol, signal_id=signal.id)
