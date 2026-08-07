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
from app.core.enums import SignalDirection
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
from app.signals.data_quality import compute_analysis_data_quality
from app.intelligence import InstitutionalIntelligenceOrchestrator
from app.intelligence.data_quality import evaluate_data_quality
from app.intelligence.types import InstitutionalContext
from app.market_regime import MarketRegimeEngine, to_legacy_regime_snapshot
from app.market_regime.types import MarketRegimeSnapshot
from app.signals.regime import RegimeSnapshot, log_regime_degraded, regime_from_indicators
from app.signals.risk import RiskConfig, RiskManager, tp_multipliers_from_settings
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
        providers: dict[str, MarketDataProvider] | None = None,
        settings: Settings | None = None,
        llm_service: LLMService | None = None,
        sentiment_service: SentimentService | None = None,
        indicator_engine: IndicatorEngine | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = provider
        self._providers = providers or {provider.name: provider}
        self._llm = llm_service
        self._sentiment = sentiment_service
        self._indicators = indicator_engine or IndicatorEngine(
            min_candles=self._settings.min_candles_required
        )
        self._regime_engine = MarketRegimeEngine(
            self._settings, indicator_engine=self._indicators
        )
        self._intel = InstitutionalIntelligenceOrchestrator(self._settings)
        self._regime_cache: RegimeSnapshot | None = None
        self._market_regime_cache: MarketRegimeSnapshot | None = None
        self._intel_cache: InstitutionalContext | None = None

    async def resolve_market_regime_snapshot(
        self,
        *,
        refresh: bool = False,
        symbol: str | None = None,
    ) -> MarketRegimeSnapshot | None:
        """Full global market regime snapshot (cached per scan cycle)."""
        if not self._settings.market_regime_enabled and not self._settings.regime_filter_enabled:
            return None
        if self._market_regime_cache is not None and not refresh:
            return self._market_regime_cache
        if self._settings.market_regime_enabled:
            snap = await self._regime_engine.resolve(
                self._provider, symbol=symbol, refresh=refresh
            )
            self._market_regime_cache = snap
            self._regime_cache = to_legacy_regime_snapshot(snap)
            if not snap.available:
                log_regime_degraded(snap.detail)
            return snap
        # Legacy single-TF BTC path when market_regime_enabled=false.
        legacy = await self._resolve_legacy_regime(refresh=refresh)
        self._regime_cache = legacy
        return None

    async def resolve_market_regime(self, *, refresh: bool = False) -> RegimeSnapshot:
        """BTC-/Market-Regime fuer den aktuellen Scan-Zyklus (gecacht, legacy shape)."""
        if not self._settings.regime_filter_enabled and not self._settings.market_regime_enabled:
            return RegimeSnapshot(None, "regime_filter_disabled", False)
        if self._regime_cache is not None and not refresh:
            return self._regime_cache
        await self.resolve_market_regime_snapshot(refresh=refresh)
        if self._regime_cache is not None:
            return self._regime_cache
        return RegimeSnapshot(None, "regime_unavailable", False)

    async def _resolve_legacy_regime(self, *, refresh: bool = False) -> RegimeSnapshot:
        if self._regime_cache is not None and not refresh:
            return self._regime_cache
        symbol = self._settings.regime_btc_symbol.upper()
        timeframe = self._settings.regime_timeframe
        try:
            series = await self._provider.get_candles(
                symbol,
                timeframe,
                limit=self._settings.candle_limit,
            )
            if series.is_empty:
                log_regime_degraded("btc_candles_empty")
                snapshot = RegimeSnapshot(None, "btc_candles_empty", False)
            else:
                indicators = self._indicators.compute(
                    series.to_dataframe(), timeframe, symbol=symbol
                )
                snapshot = regime_from_indicators(indicators)
                if not snapshot.available:
                    log_regime_degraded(snapshot.detail)
        except Exception as exc:
            log_regime_degraded(str(exc))
            snapshot = RegimeSnapshot(None, f"btc_regime_error: {exc}", False)
        self._regime_cache = snapshot
        return snapshot

    def clear_regime_cache(self) -> None:
        self._regime_cache = None
        self._market_regime_cache = None
        self._intel_cache = None
        self._regime_engine.clear_cache()

    async def resolve_market_intelligence(
        self,
        *,
        candle_data_quality: float = 100.0,
        exchange_ok: bool = True,
        refresh: bool = False,
        symbol: str | None = None,
    ) -> InstitutionalContext:
        """Market Intelligence (KB Parts 2–4/7–8) — mandatory before coin scoring."""
        if self._intel_cache is not None and not refresh:
            return self._intel_cache
        market_snap = await self.resolve_market_regime_snapshot(
            refresh=refresh, symbol=symbol
        )
        ctx = self._intel.build_market_intelligence(
            market_snap,
            candle_data_quality=candle_data_quality,
            exchange_ok=exchange_ok,
        )
        self._intel_cache = ctx
        return ctx

    @property
    def provider(self) -> MarketDataProvider:
        """Der primaere Marktdaten-Provider, z. B. fuer Statusabfragen."""
        return self._provider

    def _provider_for(self, exchange: str | None) -> MarketDataProvider:
        """Provider fuer eine Boerse waehlen; Fallback ist der primaere Provider."""
        if exchange:
            resolved = self._providers.get(exchange.lower().strip())
            if resolved is not None:
                return resolved
        return self._provider

    async def analyze(
        self,
        symbol: str,
        *,
        timeframes: list[str] | None = None,
        session: AsyncSession | None = None,
        exchange: str | None = None,
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
            exchange: Boerse fuer Kerzen (z. B. ``binance``). Ohne Angabe aus DB.
            use_llm: Ueberschreibt ``ENABLE_LLM_ANALYSIS`` fuer diesen Aufruf.
        """
        normalized = symbol.upper().strip()
        target_timeframes = timeframes or self._settings.timeframes

        resolved_exchange = exchange
        if resolved_exchange is None and session is not None:
            asset = await AssetRepository(session).get_by_symbol(normalized)
            if asset is not None:
                resolved_exchange = asset.exchange

        provider = self._provider_for(resolved_exchange)

        info = await provider.get_symbol_info(normalized)
        if not info.is_active:
            raise MarketDataError(
                f"Handelspaar {normalized} wird derzeit nicht gehandelt.",
                detail=f"Status beim Provider {provider.name}: inaktiv",
            )

        # KB Parts 2/4: Market Intelligence MUST finish before any coin analysis.
        intel = await self.resolve_market_intelligence(
            candle_data_quality=100.0,
            exchange_ok=True,
            symbol=normalized,
        )
        market_snap = intel.market_regime
        regime_snapshot = self._regime_cache or await self.resolve_market_regime()
        hard_veto = (
            self._settings.regime_filter_enabled
            and self._settings.market_regime_hard_veto
            and regime_snapshot.available
        )
        market_regime = regime_snapshot.regime if hard_veto else None

        series_map = await provider.get_multi_timeframe_candles(
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

        # Refresh data-quality snapshot with coin candle quality (Part 7).
        intel.data_quality = evaluate_data_quality(
            candle_data_quality=data_quality,
            regime=market_snap,
            exchange_ok=True,
            min_quality=float(
                getattr(self._settings, "institutional_min_data_quality", 70.0)
            ),
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
            market_regime=market_regime,
        )
        result.coin_score = result.score
        if self._settings.market_regime_enabled and market_snap is not None:
            blended = self._regime_engine.score_calculator.blend(
                result.score, result.direction, market_snap
            )
            result.score = blended.final_score
            result.market_context = market_snap.to_context_dict()
            result.market_context["blend"] = {
                "coinScore": blended.coin_score,
                "finalScore": blended.final_score,
                "globalScore": blended.global_score,
                "fundingScore": blended.funding_score,
                "oiScore": blended.oi_score,
                "liquidationScore": blended.liquidation_score,
                "weights": blended.weights_used,
                "detail": blended.detail,
            }
            if result.is_actionable and abs(blended.final_score - blended.coin_score) >= 0.5:
                result.reasons.append(
                    f"Market regime blend: coin {blended.coin_score:.1f} → "
                    f"final {blended.final_score:.1f} ({market_snap.bias.label})"
                )
            # Re-apply score gates on the blended score (engine gated coin score).
            blend_block = self._blended_score_no_trade(result)
            if blend_block is not None:
                result.no_trade_reason = blend_block
                result.direction = SignalDirection.NO_TRADE
                result.reasons.append(blend_block)
        else:
            result.market_context = result.market_context or {}

        if (
            self._settings.regime_filter_enabled
            and self._settings.market_regime_hard_veto
            and self._settings.market_regime_fail_closed
            and not regime_snapshot.available
            and result.direction.is_actionable
        ):
            detail = regime_snapshot.detail or "regime_unavailable"
            block = f"Market regime unavailable — entries blocked ({detail})"
            result.direction = SignalDirection.NO_TRADE
            result.no_trade_reason = block
            result.reasons.append(block)
            log_regime_degraded(detail)

        explain = self._intel.finalize_trade(result, intel)
        result.confidence_pct = explain.confidence_pct
        result.explainability = explain.to_dict()
        if result.market_context is None:
            result.market_context = {}
        result.market_context["intelligence"] = intel.to_desk_dict()
        result.market_context["explainability"] = explain.to_dict()

        logger.info(
            "analysis_completed",
            symbol=normalized,
            direction=result.direction.value,
            score=result.score,
            coin_score=result.coin_score,
            market_bias=market_snap.bias.value if market_snap and market_snap.available else None,
            confidence=result.confidence.value,
            confidence_pct=result.confidence_pct,
            decision=explain.decision.value,
            data_quality=result.data_quality,
            timeframes=result.analyzed_timeframes,
            skipped_timeframes=skipped,
        )

        outcome = AnalysisOutcome(
            result=result,
            price_precision=info.price_precision,
            skipped_timeframes=skipped,
            chart_series=self._select_chart_series(series_map),
        )

        await self._attach_llm_summary(outcome, use_llm=use_llm)

        if persist and session is not None:
            await self._persist(
                session,
                outcome,
                info,
                indicator_sets,
                series_map,
                version_id,
                exchange=provider.name,
            )

        return outcome

    def _select_chart_series(
        self, series_map: dict[str, CandleSeries]
    ) -> CandleSeries | None:
        """Kerzen fuer das Telegram-Signal-Chart — immer 4h, sonst Fallback."""
        preferred_tf = "4h"
        preferred = series_map.get(preferred_tf)
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

        data_quality = compute_analysis_data_quality(
            qualities,
            indicator_sets=indicator_sets,
            primary_timeframe=self._settings.primary_timeframe,
        )

        return indicator_sets, skipped, data_quality

    def _build_engine(self, weights: StrategyWeights) -> SignalEngine:
        risk_manager = RiskManager(
            RiskConfig(
                atr_multiplier=self._settings.atr_multiplier,
                min_risk_reward_ratio=self._settings.min_risk_reward_ratio,
                max_risk_percent=self._settings.max_risk_percent,
                min_stop_distance_percent=self._settings.min_stop_distance_percent,
                max_stop_distance_percent=self._settings.max_stop_distance_percent,
                reject_wide_stops=self._settings.reject_wide_stops,
                reference_capital=self._settings.reference_capital,
                tp_multipliers=tp_multipliers_from_settings(self._settings),
            )
        )
        config = signal_engine_config_from_settings(
            self._settings,
            weights=weights,
            enable_sentiment=self._settings.enable_sentiment,
        )
        return SignalEngine(config, risk_manager)

    def _blended_score_no_trade(self, result: SignalResult) -> str | None:
        """Score gates that must hold after regime blend mutates ``result.score``."""
        if not result.direction.is_actionable:
            return None
        score = float(result.score)
        if result.direction.is_long and score < self._settings.signal_min_score:
            return (
                f"Blended long score {score:.1f} below minimum "
                f"{self._settings.signal_min_score:.1f}"
            )
        if result.direction.is_short and score > self._settings.signal_short_max_score:
            return (
                f"Blended short score {score:.1f} above maximum "
                f"{self._settings.signal_short_max_score:.1f}"
            )
        if result.direction.is_short and score <= self._settings.signal_short_min_score:
            return (
                f"Blended short score {score:.1f} in exhaustion band "
                f"(minimum {self._settings.signal_short_min_score:.0f})"
            )
        return None

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
        *,
        exchange: str,
    ) -> None:
        assets = AssetRepository(session)
        signals = SignalRepository(session)

        asset = await assets.get_or_create(info, exchange=exchange)  # type: ignore[arg-type]
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
