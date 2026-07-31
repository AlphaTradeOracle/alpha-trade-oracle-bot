"""BTC-Marktregime fuer richtungsabhaengige Entry-Gates.

Regime wird auf dem konfigurierten BTC-Timeframe (Default 4h) aus Close vs.
EMA20/EMA50 und Supertrend abgeleitet. Fehlen BTC-Daten, wird nicht gefiltert
(degradation graceful).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.enums import SignalDirection
from app.core.logging import get_logger
from app.indicators.engine import IndicatorSet

logger = get_logger(__name__)


class MarketRegime(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class RegimeSnapshot:
    """Ergebnis der Regime-Bestimmung."""

    regime: MarketRegime | None
    detail: str
    available: bool


def regime_from_indicators(indicators: IndicatorSet) -> RegimeSnapshot:
    """Regime aus einem BTC-Indikatorsatz ableiten."""
    close = indicators.close_price
    ema20 = indicators.ema_20
    ema50 = indicators.ema_50

    if ema20 is None or ema50 is None or close <= 0:
        return RegimeSnapshot(
            None,
            "btc_regime_insufficient_ema_data",
            False,
        )

    bullish_votes = 0
    bearish_votes = 0

    if close > ema20:
        bullish_votes += 1
    else:
        bearish_votes += 1

    if close > ema50:
        bullish_votes += 1
    else:
        bearish_votes += 1

    if ema20 > ema50:
        bullish_votes += 1
    else:
        bearish_votes += 1

    st_dir = indicators.supertrend_direction
    if st_dir is not None:
        if st_dir > 0:
            bullish_votes += 1
        elif st_dir < 0:
            bearish_votes += 1

    if bullish_votes >= 3:
        regime = MarketRegime.BULLISH
    elif bearish_votes >= 3:
        regime = MarketRegime.BEARISH
    else:
        regime = MarketRegime.NEUTRAL

    detail = (
        f"btc close={close:.4g} ema20={ema20:.4g} ema50={ema50:.4g} "
        f"votes=+{bullish_votes}/-{bearish_votes} regime={regime.value}"
    )
    return RegimeSnapshot(regime, detail, True)


def direction_allowed_by_regime(
    regime: MarketRegime | None,
    direction: SignalDirection,
) -> bool:
    """True wenn die Richtung zum Regime passt oder Regime unbekannt ist."""
    if regime is None or not direction.is_actionable:
        return True
    if regime is MarketRegime.BULLISH and direction.is_short:
        return False
    if regime is MarketRegime.BEARISH and direction.is_long:
        return False
    return True


def regime_block_reason(
    regime: MarketRegime,
    direction: SignalDirection,
) -> str | None:
    """NO_TRADE-/Skip-Text wenn Regime die Richtung blockiert."""
    if direction_allowed_by_regime(regime, direction):
        return None
    if regime is MarketRegime.BULLISH and direction.is_short:
        return (
            f"Market regime bullish ({regime.value}) — no new short entries"
        )
    if regime is MarketRegime.BEARISH and direction.is_long:
        return (
            f"Market regime bearish ({regime.value}) — no new long entries"
        )
    return None


def log_regime_degraded(detail: str) -> None:
    logger.warning("regime_filter_degraded", detail=detail)


__all__ = [
    "MarketRegime",
    "RegimeSnapshot",
    "direction_allowed_by_regime",
    "log_regime_degraded",
    "regime_block_reason",
    "regime_from_indicators",
]
