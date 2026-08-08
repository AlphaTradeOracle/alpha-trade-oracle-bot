"""Signal-Engine: fuehrt Bewertung, Richtungsentscheidung und Risiko zusammen.

Die Engine ist deterministisch und ohne I/O. Sie wird von der Live-Analyse und
vom Backtest identisch verwendet.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.config import Settings
from app.core.enums import (
    Confidence,
    MarketPhase,
    ScoreCategory,
    SignalDirection,
)
from app.core.time import timeframe_to_timedelta, utc_now
from app.indicators.engine import IndicatorSet
from app.signals.multi_timeframe import (
    aggregate_category,
    assess_timeframes,
    describe_timeframe_trends,
    determine_market_phase,
    multi_timeframe_agreement,
)
from app.signals.risk import RiskConfig, RiskManager
from app.signals.regime import MarketRegime, direction_allowed_by_regime, regime_block_reason
from app.signals.types import ScoreComponent, SignalResult, TimeframeAssessment
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

#: Schwellen der Richtungsentscheidung auf dem 0..100-Score.
STRONG_LONG_SCORE = 80.0
LONG_SCORE = 65.0
SHORT_SCORE = 35.0
STRONG_SHORT_SCORE = 20.0

#: Zusaetzlich erforderliche Timeframe-Uebereinstimmung fuer STRONG-Signale.
STRONG_AGREEMENT = 0.6

#: Mindest-Datenqualitaet, unter der grundsaetzlich NO_TRADE gilt.
MIN_DATA_QUALITY = 60.0

#: Schwellen der Konfidenzeinstufung.
HIGH_CONFIDENCE_DEVIATION = 25.0
MEDIUM_CONFIDENCE_DEVIATION = 12.0
HIGH_CONFIDENCE_DATA_QUALITY = 85.0
MEDIUM_CONFIDENCE_DATA_QUALITY = 70.0


@dataclass(frozen=True)
class SignalEngineConfig:
    """Konfiguration der Signal-Engine."""

    weights: StrategyWeights = DEFAULT_WEIGHTS
    primary_timeframe: str = "1h"
    confirmation_timeframe: str = "4h"
    min_risk_reward_ratio: float = 2.0
    max_atr_percent: float = 12.0
    expiry_multiplier: int = 4
    enable_sentiment: bool = False
    block_range_market: bool = True
    min_adx: float = 30.0
    min_adx_soft: float = 20.0
    #: Dispatch-band thresholds — high-conviction setups use soft ADX/range gates.
    min_score: float = 70.0
    short_max_score: float = 30.0
    rsi_long_max: float = 75.0
    rsi_short_min: float = 33.0
    short_min_score: float = 18.0
    regime_filter_enabled: bool = True
    strategy_version_label: str = "default:1"


def signal_engine_config_from_settings(
    settings: Settings,
    *,
    weights: StrategyWeights = DEFAULT_WEIGHTS,
    enable_sentiment: bool | None = None,
) -> SignalEngineConfig:
    """SignalEngineConfig aus den zentralen Settings ableiten."""
    return SignalEngineConfig(
        weights=weights,
        primary_timeframe=settings.primary_timeframe,
        confirmation_timeframe="4h",
        min_risk_reward_ratio=settings.min_risk_reward_ratio,
        max_atr_percent=settings.max_atr_percent,
        expiry_multiplier=settings.signal_expiry_multiplier,
        enable_sentiment=(
            settings.enable_sentiment if enable_sentiment is None else enable_sentiment
        ),
        block_range_market=settings.signal_block_range_market,
        min_adx=settings.signal_min_adx,
        min_adx_soft=settings.signal_min_adx_soft,
        min_score=settings.signal_min_score,
        short_max_score=settings.signal_short_max_score,
        rsi_long_max=settings.signal_rsi_long_max,
        rsi_short_min=settings.signal_rsi_short_min,
        short_min_score=settings.signal_short_min_score,
        regime_filter_enabled=settings.regime_filter_enabled,
    )


class SignalEngine:
    """Erzeugt aus Indikatorsaetzen ein vollstaendiges, begruendetes Signal."""

    def __init__(
        self,
        config: SignalEngineConfig | None = None,
        risk_manager: RiskManager | None = None,
    ) -> None:
        self._config = config or SignalEngineConfig()
        # Ohne Sentiment-Daten wird das Gewicht umverteilt, damit die Summe 1.0 bleibt.
        self._weights = (
            self._config.weights
            if self._config.enable_sentiment
            else self._config.weights.without_sentiment()
        )
        self._risk_manager = risk_manager or RiskManager(
            RiskConfig(min_risk_reward_ratio=self._config.min_risk_reward_ratio)
        )

    @property
    def weights(self) -> StrategyWeights:
        return self._weights

    def generate(
        self,
        symbol: str,
        indicator_sets: dict[str, IndicatorSet],
        *,
        data_quality: float = 100.0,
        sentiment_score: float | None = None,
        now: datetime | None = None,
        market_regime: MarketRegime | None = None,
    ) -> SignalResult:
        """Signal fuer ein Symbol erzeugen.

        Args:
            symbol: Handelspaar, z. B. ``BTCUSDT``.
            indicator_sets: Indikatorsatz je Timeframe.
            data_quality: 0..100, aus Historienlaenge und Datenluecken.
            sentiment_score: Optionaler Wert in [-100, +100]. ``None`` bedeutet
                „keine Daten" — es wird dann kein Wert erfunden.
            now: Referenzzeit, im Backtest der Zeitpunkt der Kerze.
        """
        if not indicator_sets:
            raise ValueError("Es wurde kein Indikatorsatz uebergeben")

        created_at = now or utc_now()
        primary_timeframe = self._resolve_primary_timeframe(indicator_sets)
        assessments = assess_timeframes(indicator_sets)

        components = self._build_components(assessments, sentiment_score)
        score = self._weighted_score(components)
        agreement = self._agreement_value(components)

        direction = self._determine_direction(score, agreement)
        primary_indicators = assessments[primary_timeframe].indicators

        risk = self._risk_manager.calculate(
            direction,
            primary_indicators,
            confirmation_timeframe=self._config.confirmation_timeframe,
        )

        # R:R ist nur NO_TRADE-Gate, nicht Score-Komponente (direction-blind bei 3.27%% Gewicht).
        components.append(self._risk_reward_info_component(risk))
        market_phase = determine_market_phase(assessments, primary_timeframe)

        no_trade_reason = self._check_no_trade(
            direction,
            primary_indicators,
            risk,
            data_quality,
            market_phase=market_phase,
            score=score,
            market_regime=market_regime,
        )
        if no_trade_reason is not None:
            direction = SignalDirection.NO_TRADE

        confidence = self._determine_confidence(score, agreement, data_quality)
        reasons, counter_arguments = self._build_arguments(direction, components, assessments, risk)

        expires_at = created_at + self._expiry_duration(primary_timeframe)
        indicators_used = sorted(
            {name for a in assessments.values() for name in a.indicators.indicators_used()}
        )

        result = SignalResult(
            symbol=symbol.upper(),
            created_at=created_at,
            expires_at=expires_at,
            direction=direction,
            score=round(score, 2),
            confidence=confidence,
            market_phase=market_phase,
            primary_timeframe=primary_timeframe,
            analyzed_timeframes=sorted(indicator_sets, key=_timeframe_sort_key),
            reference_price=primary_indicators.close_price,
            data_quality=round(data_quality, 2),
            components=components,
            assessments=assessments,
            risk=risk,
            reasons=reasons,
            counter_arguments=counter_arguments,
            indicators_used=indicators_used,
            no_trade_reason=no_trade_reason,
        )
        result.fingerprint = self._fingerprint(result)
        return result

    # --- Score-Aufbau -----------------------------------------------------

    def _build_components(
        self,
        assessments: dict[str, TimeframeAssessment],
        sentiment_score: float | None,
    ) -> list[ScoreComponent]:
        weights = self._weights.as_dict()
        agreement_raw, agreement_detail = multi_timeframe_agreement(assessments)

        components = [
            ScoreComponent(
                category=ScoreCategory.TREND,
                raw_score=aggregate_category(assessments, "trend_score"),
                weight=weights[ScoreCategory.TREND],
                detail=self._detail_for(assessments, 0),
            ),
            ScoreComponent(
                category=ScoreCategory.MOMENTUM,
                raw_score=aggregate_category(assessments, "momentum_score"),
                weight=weights[ScoreCategory.MOMENTUM],
                detail=self._detail_for(assessments, 1),
            ),
            ScoreComponent(
                category=ScoreCategory.VOLUME,
                raw_score=aggregate_category(assessments, "volume_score"),
                weight=weights[ScoreCategory.VOLUME],
                detail=self._detail_for(assessments, 2),
            ),
            ScoreComponent(
                category=ScoreCategory.VOLATILITY,
                raw_score=aggregate_category(assessments, "volatility_score"),
                weight=weights[ScoreCategory.VOLATILITY],
                detail=self._detail_for(assessments, 3),
            ),
            ScoreComponent(
                category=ScoreCategory.MARKET_STRUCTURE,
                raw_score=aggregate_category(assessments, "structure_score"),
                weight=weights[ScoreCategory.MARKET_STRUCTURE],
                detail=self._detail_for(assessments, 4),
            ),
            ScoreComponent(
                category=ScoreCategory.MULTI_TIMEFRAME,
                raw_score=agreement_raw,
                weight=weights[ScoreCategory.MULTI_TIMEFRAME],
                detail=agreement_detail,
            ),
        ]

        # Auch bei Gewicht 0 auffuehren, damit das Breakdown zeigt, dass Sentiment
        # aktiv, aber ungewichtet ist. Der Beitrag zum Score bleibt 0.
        if self._config.enable_sentiment:
            sentiment_weight = weights[ScoreCategory.SENTIMENT]
            if sentiment_score is None:
                # Keine Daten: neutral bewerten, aber transparent benennen.
                components.append(
                    ScoreComponent(
                        category=ScoreCategory.SENTIMENT,
                        raw_score=0.0,
                        weight=sentiment_weight,
                        detail="No sentiment data available (scored neutral)",
                    )
                )
            else:
                components.append(
                    ScoreComponent(
                        category=ScoreCategory.SENTIMENT,
                        raw_score=max(-100.0, min(100.0, sentiment_score)),
                        weight=sentiment_weight,
                        detail=f"Sentiment raw score {sentiment_score:+.1f}",
                    )
                )

        # R:R fließt nicht in den Score ein — nur als NO_TRADE-Gate (siehe _check_no_trade).
        return components

    def _risk_reward_info_component(self, risk: object | None) -> ScoreComponent:
        """Informativer R:R-Eintrag im Breakdown ohne Score-Beitrag."""
        if risk is None:
            detail = "No risk/reward calculated (no tradeable setup)"
            ratio = 0.0
        else:
            ratio = float(getattr(risk, "risk_reward_ratio", 0.0))
            detail = (
                f"Risk/reward {ratio:.2f} "
                f"(minimum {self._config.min_risk_reward_ratio:.2f}, gate only)"
            )
        return ScoreComponent(
            category=ScoreCategory.RISK_REWARD,
            raw_score=0.0,
            weight=0.0,
            detail=detail,
        )

    @staticmethod
    def _weighted_score(components: list[ScoreComponent]) -> float:
        """Gewichtete Summe der Rohwerte auf 0..100 abbilden. 50 ist neutral."""
        raw_total = sum(component.weighted_score for component in components)
        return max(0.0, min(100.0, (raw_total + 100.0) / 2.0))

    @staticmethod
    def _agreement_value(components: list[ScoreComponent]) -> float:
        component = next(
            (c for c in components if c.category == ScoreCategory.MULTI_TIMEFRAME), None
        )
        return component.raw_score / 100.0 if component else 0.0

    # --- Entscheidungen ---------------------------------------------------

    @staticmethod
    def _determine_direction(score: float, agreement: float) -> SignalDirection:
        if score >= STRONG_LONG_SCORE and agreement >= STRONG_AGREEMENT:
            return SignalDirection.STRONG_LONG
        if score >= LONG_SCORE:
            return SignalDirection.LONG
        if score <= STRONG_SHORT_SCORE and agreement <= -STRONG_AGREEMENT:
            return SignalDirection.STRONG_SHORT
        if score <= SHORT_SCORE:
            return SignalDirection.SHORT
        return SignalDirection.NEUTRAL

    def _check_no_trade(
        self,
        direction: SignalDirection,
        indicators: IndicatorSet,
        risk: object | None,
        data_quality: float,
        *,
        market_phase: MarketPhase,
        score: float,
        market_regime: MarketRegime | None = None,
    ) -> str | None:
        """Harte Ausschlusskriterien. Sie ueberschreiben jede Richtung."""
        if not direction.is_actionable:
            return None

        if (
            direction.is_short
            and score <= self._config.short_min_score
        ):
            return (
                f"Short score {score:.1f} in exhaustion band "
                f"(minimum {self._config.short_min_score:.0f})"
            )

        if self._config.regime_filter_enabled and market_regime is not None:
            blocked = regime_block_reason(market_regime, direction)
            if blocked is not None:
                return blocked

        if data_quality < MIN_DATA_QUALITY:
            return (
                f"Data quality {data_quality:.2f} is below the minimum "
                f"of {MIN_DATA_QUALITY:.2f}"
            )

        high_conviction = self._is_high_conviction(direction, score)
        adx_floor = (
            self._config.min_adx_soft if high_conviction else self._config.min_adx
        )

        if self._config.block_range_market and market_phase is MarketPhase.RANGE:
            # High-conviction band may trade mild ranges if ADX clears the soft floor.
            if not high_conviction or (
                indicators.adx_14 is not None and indicators.adx_14 < adx_floor
            ):
                adx_text = (
                    f" (ADX {indicators.adx_14:.1f})"
                    if indicators.adx_14 is not None
                    else ""
                )
                return f"Range market without trend strength{adx_text} — no clear setup"

        if indicators.adx_14 is not None and indicators.adx_14 < adx_floor:
            return (
                f"Trend strength too low (ADX {indicators.adx_14:.1f} "
                f"below minimum {adx_floor:.1f})"
            )

        if indicators.rsi_14 is not None:
            if direction.is_long and indicators.rsi_14 > self._config.rsi_long_max:
                return (
                    f"RSI {indicators.rsi_14:.1f} overbought "
                    f"(long maximum: {self._config.rsi_long_max:.0f})"
                )
            if direction.is_short and indicators.rsi_14 < self._config.rsi_short_min:
                return (
                    f"RSI {indicators.rsi_14:.1f} oversold "
                    f"(short minimum: {self._config.rsi_short_min:.0f})"
                )

        if (
            indicators.atr_percent is not None
            and indicators.atr_percent > self._config.max_atr_percent
        ):
            return (
                f"Volatility too high (ATR {indicators.atr_percent:.2f}% "
                f"above limit {self._config.max_atr_percent:.2f}%)"
            )

        if risk is None:
            return "No reliable risk parameters available"

        ratio = float(getattr(risk, "risk_reward_ratio", 0.0))
        if ratio < self._config.min_risk_reward_ratio:
            return (
                f"Risk/reward {ratio:.2f} below the minimum "
                f"of {self._config.min_risk_reward_ratio:.2f}"
            )

        return None

    def _is_high_conviction(self, direction: SignalDirection, score: float) -> bool:
        """True when score is already in the dispatch band (long≥min / short≤max)."""
        if direction.is_long and score >= self._config.min_score:
            return True
        if direction.is_short and score <= self._config.short_max_score:
            return True
        return False

    @staticmethod
    def _determine_confidence(score: float, agreement: float, data_quality: float) -> Confidence:
        """Konfidenz bewusst getrennt vom Score.

        Ein hoher Score bei widerspruechlichen Timeframes ist weniger belastbar
        als ein mittlerer Score bei klarer Uebereinstimmung.
        """
        deviation = abs(score - 50.0)
        if (
            deviation >= HIGH_CONFIDENCE_DEVIATION
            and abs(agreement) >= STRONG_AGREEMENT
            and data_quality >= HIGH_CONFIDENCE_DATA_QUALITY
        ):
            return Confidence.HIGH
        if (
            deviation >= MEDIUM_CONFIDENCE_DEVIATION
            and data_quality >= MEDIUM_CONFIDENCE_DATA_QUALITY
        ):
            return Confidence.MEDIUM
        return Confidence.LOW

    # --- Begruendungen ----------------------------------------------------

    def _build_arguments(
        self,
        direction: SignalDirection,
        components: list[ScoreComponent],
        assessments: dict[str, TimeframeAssessment],
        risk: object | None,
    ) -> tuple[list[str], list[str]]:
        """Bestaetigungen und Gegenargumente aus den Score-Komponenten ableiten.

        Die Zuordnung folgt dem Vorzeichen relativ zur Signalrichtung: was fuer
        die Richtung spricht, wird zur Bestaetigung, alles andere zum Gegenargument.
        """
        sign = 1.0 if direction.is_long else (-1.0 if direction.is_short else 0.0)
        reasons: list[str] = []
        counters: list[str] = []

        for component in components:
            if not component.detail or component.detail in {
                "Noch nicht bewertet",
                "Not yet scored",
            }:
                continue
            aligned = component.raw_score * sign
            if sign == 0.0:
                # Neutral signal: keep everything as observation.
                reasons.append(component.detail)
            elif aligned > 5.0:
                reasons.append(component.detail)
            elif aligned < -5.0:
                counters.append(component.detail)

        for assessment in assessments.values():
            structure = assessment.indicators.structure
            if structure.failed_breakout_up and direction.is_long:
                counters.append(f"{assessment.timeframe}: failed breakout up")
            if structure.failed_breakout_down and direction.is_short:
                counters.append(f"{assessment.timeframe}: failed breakout down")
            if structure.bearish_divergence and direction.is_long:
                counters.append(f"{assessment.timeframe}: bearish divergence")
            if structure.bullish_divergence and direction.is_short:
                counters.append(f"{assessment.timeframe}: bullish divergence")

        if risk is not None:
            for warning in getattr(risk, "warnings", []):
                counters.append(str(warning))

        trends = describe_timeframe_trends(assessments)
        if trends:
            reasons.insert(0, "Trend stack: " + ", ".join(trends))

        return _unique(reasons), _unique(counters)

    # --- Hilfsfunktionen --------------------------------------------------

    def _resolve_primary_timeframe(self, indicator_sets: dict[str, IndicatorSet]) -> str:
        """Konfigurierten Setup-Timeframe verwenden, sonst den naechstbesten."""
        if self._config.primary_timeframe in indicator_sets:
            return self._config.primary_timeframe
        for candidate in ("1h", "4h", "15m", "1d"):
            if candidate in indicator_sets:
                return candidate
        return next(iter(indicator_sets))

    def _expiry_duration(self, timeframe: str) -> timedelta:
        try:
            return timeframe_to_timedelta(timeframe) * self._config.expiry_multiplier
        except ValueError:
            return timedelta(hours=4)

    @staticmethod
    def _detail_for(assessments: dict[str, TimeframeAssessment], note_index: int) -> str:
        """Begruendungstext des Setup-Timeframes bevorzugen, sonst den ersten."""
        preferred = assessments.get("1h") or next(iter(assessments.values()), None)
        if preferred is None or note_index >= len(preferred.notes):
            return ""
        return preferred.notes[note_index]

    def _fingerprint(self, result: SignalResult) -> str:
        """Stabiler Fingerprint zur Duplikaterkennung.

        Score wird auf 5er-Schritte und der Entry-Mittelpunkt auf sechs
        signifikante Stellen gerundet, damit minimale Schwankungen nicht als
        neues Signal gelten.
        """
        entry_mid = result.risk.entry_mid if result.risk else result.reference_price
        parts = [
            result.symbol,
            result.primary_timeframe,
            result.direction.value,
            str(int(result.score // 5)),
            f"{entry_mid:.6g}",
            self._config.strategy_version_label,
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _unique(values: list[str]) -> list[str]:
    """Reihenfolge erhalten, Duplikate entfernen."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _timeframe_sort_key(timeframe: str) -> int:
    from app.core.time import TIMEFRAME_MINUTES

    return TIMEFRAME_MINUTES.get(timeframe, 9999)


__all__ = [
    "MIN_DATA_QUALITY",
    "MarketPhase",
    "SignalEngine",
    "SignalEngineConfig",
    "signal_engine_config_from_settings",
]
