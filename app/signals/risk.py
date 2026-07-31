"""Risikomanagement: Entry-Zone, Stop-Loss, Take-Profit, Positionsgroesse.

Alle Ergebnisse sind ausschliesslich informativ. Es werden keine Orders erzeugt
und keine Boersen-Schnittstellen aufgerufen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.enums import SignalDirection
from app.indicators.engine import IndicatorSet
from app.signals.types import RiskParameters

if TYPE_CHECKING:
    from app.core.config import Settings


def tp_multipliers_from_settings(settings: Settings | None = None) -> tuple[float, float, float]:
    """TP-Multiples aus Settings oder Modul-Default."""
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    return settings.parsed_tp_multipliers

#: Vielfache des Risikoabstands R fuer die drei Take-Profit-Ziele.
#: Default 1/2/3R (MFE-Analyse: ≥2R nur ~24%% live; engere Leiter bankt oefter).
TP_MULTIPLIERS = (1.0, 2.0, 3.0)
DEFAULT_TP_MULTIPLIERS = TP_MULTIPLIERS
LEGACY_TP_MULTIPLIERS = (2.0, 4.0, 6.0)

#: Halbe Breite der Entry-Zone in ATR.
ENTRY_ZONE_ATR_FRACTION = 0.25

#: Puffer, mit dem ein Stop unter einen Support (bzw. ueber einen Widerstand) gelegt wird.
LEVEL_BUFFER_PERCENT = 0.15

#: Maximale Entfernung eines Levels, damit es fuer die Stop-Platzierung zaehlt.
#
# Der Wert muss groesser sein als ``RiskConfig.atr_multiplier``, sonst liegt ein
# als relevant erkanntes Level immer schon oberhalb des ATR-Stops und die
# Ausrichtung an der Marktstruktur greift nie. Gefaehrlich ist genau der Fall,
# in dem ein Level *knapp unter* dem ATR-Stop liegt: der Kurs laeuft das Level
# an, raeumt dabei aber zuerst den Stop ab.
LEVEL_RELEVANCE_ATR = 2.5


@dataclass(frozen=True)
class RiskConfig:
    """Parameter der Risikoberechnung."""

    atr_multiplier: float = 1.5
    min_risk_reward_ratio: float = 2.0
    max_risk_percent: float = 1.0
    min_stop_distance_percent: float = 0.3
    max_stop_distance_percent: float = 8.0
    #: Verwerfen statt nur kennzeichnen, wenn der Stop zu weit ist.
    reject_wide_stops: bool = False
    reference_capital: float = 10_000.0
    tp_multipliers: tuple[float, float, float] = DEFAULT_TP_MULTIPLIERS


class RiskManager:
    """Berechnet Risikoparameter aus Kurslage, ATR und Marktstruktur."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self._config = config or RiskConfig()

    @property
    def config(self) -> RiskConfig:
        return self._config

    def calculate(
        self,
        direction: SignalDirection,
        indicators: IndicatorSet,
        *,
        confirmation_timeframe: str = "4h",
    ) -> RiskParameters | None:
        """Risikoparameter berechnen, oder ``None`` wenn das nicht sinnvoll moeglich ist."""
        if not direction.is_actionable:
            return None

        price = indicators.close_price
        atr_value = indicators.atr_14
        if price <= 0 or atr_value is None or atr_value <= 0:
            return None

        is_long = direction.is_long
        warnings: list[str] = []

        entry_low, entry_high = self._entry_zone(price, atr_value)
        entry_reference = entry_low if is_long else entry_high

        stop_loss, stop_note = self._stop_loss(
            entry_reference, atr_value, indicators, is_long=is_long
        )
        if stop_note:
            warnings.append(stop_note)

        stop_distance = abs(entry_reference - stop_loss)
        if stop_distance <= 0:
            return None

        stop_distance_percent = stop_distance / entry_reference * 100.0
        if (
            self._config.reject_wide_stops
            and stop_distance_percent > self._config.max_stop_distance_percent
        ):
            return None

        stop_loss, stop_distance, stop_distance_percent, clamp_note = self._enforce_stop_bounds(
            entry_reference, stop_loss, stop_distance, stop_distance_percent, is_long=is_long
        )
        if clamp_note:
            warnings.append(clamp_note)

        take_profits = self._take_profits(
            entry_reference, stop_distance, indicators, is_long=is_long
        )

        # Referenz fuer das R:R ist TP2 — TP1 ist zu konservativ, TP3 zu optimistisch.
        risk_reward = abs(take_profits[1] - entry_reference) / stop_distance

        position_size = self._position_size(stop_distance)

        invalidation = self._invalidation_note(stop_loss, confirmation_timeframe, is_long=is_long)

        if risk_reward < self._config.min_risk_reward_ratio:
            warnings.append(
                f"Chance-Risiko-Verhaeltnis {risk_reward:.2f} unter dem Minimum "
                f"von {self._config.min_risk_reward_ratio:.2f}"
            )

        return RiskParameters(
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=stop_loss,
            take_profit_1=take_profits[0],
            take_profit_2=take_profits[1],
            take_profit_3=take_profits[2],
            risk_reward_ratio=risk_reward,
            risk_percent=self._config.max_risk_percent,
            suggested_position_size=position_size,
            stop_distance_percent=stop_distance_percent,
            invalidation_note=invalidation,
            warnings=warnings,
        )

    # --- Einzelschritte ---------------------------------------------------

    @staticmethod
    def _entry_zone(price: float, atr_value: float) -> tuple[float, float]:
        """Entry-Zone symmetrisch um den Referenzkurs, halbe ATR-Breite."""
        offset = atr_value * ENTRY_ZONE_ATR_FRACTION
        return price - offset, price + offset

    def _stop_loss(
        self,
        entry: float,
        atr_value: float,
        indicators: IndicatorSet,
        *,
        is_long: bool,
    ) -> tuple[float, str | None]:
        """ATR-basierter Stop, an Support/Resistance ausgerichtet.

        Ein Stop unmittelbar oberhalb eines Supports wird ueberproportional oft
        abgeraeumt. Liegt ein relevanter Support in Reichweite, wird der Stop
        daher knapp darunter platziert.
        """
        atr_stop = (
            entry - atr_value * self._config.atr_multiplier
            if is_long
            else entry + atr_value * self._config.atr_multiplier
        )

        structure = indicators.structure
        buffer = 1.0 - LEVEL_BUFFER_PERCENT / 100.0

        # Nur nach aussen verschieben, nie nach innen: ein engerer Stop als der
        # ATR-Abstand wuerde die Volatilitaet des Marktes ignorieren.
        if is_long and structure.nearest_support is not None:
            support = structure.nearest_support
            if support < entry and (entry - support) <= atr_value * LEVEL_RELEVANCE_ATR:
                candidate = support * buffer
                if candidate < atr_stop:
                    return candidate, "Stop unter den naechsten Support gelegt"

        if not is_long and structure.nearest_resistance is not None:
            resistance = structure.nearest_resistance
            if resistance > entry and (resistance - entry) <= atr_value * LEVEL_RELEVANCE_ATR:
                candidate = resistance * (1.0 + LEVEL_BUFFER_PERCENT / 100.0)
                if candidate > atr_stop:
                    return candidate, "Stop ueber den naechsten Widerstand gelegt"

        return atr_stop, None

    def _enforce_stop_bounds(
        self,
        entry: float,
        stop_loss: float,
        stop_distance: float,
        stop_distance_percent: float,
        *,
        is_long: bool,
    ) -> tuple[float, float, float, str | None]:
        """Zu enge Stops aufweiten, zu weite kennzeichnen."""
        if stop_distance_percent < self._config.min_stop_distance_percent:
            required = entry * self._config.min_stop_distance_percent / 100.0
            stop_loss = entry - required if is_long else entry + required
            return (
                stop_loss,
                required,
                self._config.min_stop_distance_percent,
                f"Stop war zu eng und wurde auf "
                f"{self._config.min_stop_distance_percent:.2f}% aufgeweitet",
            )

        if stop_distance_percent > self._config.max_stop_distance_percent:
            # Nicht verschieben, sondern kennzeichnen: ein kuenstlich enger Stop
            # waere in einem volatilen Markt gefaehrlicher als ein weiter. Das
            # Dollar-Risiko begrenzt das risikonormierte Sizing; ein harter
            # Reject laeuft ueber ``RiskConfig.reject_wide_stops``.
            return (
                stop_loss,
                stop_distance,
                stop_distance_percent,
                f"Stop-Abstand {stop_distance_percent:.2f}% ist ungewoehnlich weit",
            )

        return stop_loss, stop_distance, stop_distance_percent, None

    @staticmethod
    def targets_from_stop(
        entry: float,
        stop_loss: float,
        *,
        is_long: bool,
        multipliers: tuple[float, float, float] = DEFAULT_TP_MULTIPLIERS,
    ) -> tuple[float, float, float]:
        """TP1/TP2/TP3 als reine R-Multiples ohne Struktur-Snap."""
        distance = abs(entry - stop_loss)
        if distance <= 0:
            return entry, entry, entry
        if is_long:
            return (
                entry + distance * multipliers[0],
                entry + distance * multipliers[1],
                entry + distance * multipliers[2],
            )
        return (
            entry - distance * multipliers[0],
            entry - distance * multipliers[1],
            entry - distance * multipliers[2],
        )

    def _take_profits(
        self,
        entry: float,
        stop_distance: float,
        indicators: IndicatorSet,
        *,
        is_long: bool,
    ) -> tuple[float, float, float]:
        """Take-Profit-Ziele als Vielfache von R, an Leveln ausgerichtet.

        Liegt ein Widerstand kurz vor einem Ziel, wird das Ziel knapp darunter
        gezogen — ein Ziel jenseits eines starken Levels wird selten erreicht.
        """
        structure = indicators.structure
        levels = (
            sorted(structure.resistances) if is_long else sorted(structure.supports, reverse=True)
        )
        buffer = 1.0 - LEVEL_BUFFER_PERCENT / 100.0

        targets: list[float] = []
        for multiplier in self._config.tp_multipliers:
            raw = (
                entry + stop_distance * multiplier
                if is_long
                else entry - stop_distance * multiplier
            )
            adjusted = raw

            for level in levels:
                if is_long and entry < level < raw:
                    candidate = level * buffer
                    if candidate > entry:
                        adjusted = candidate
                    break
                if not is_long and raw < level < entry:
                    candidate = level * (1.0 + LEVEL_BUFFER_PERCENT / 100.0)
                    if candidate < entry:
                        adjusted = candidate
                    break

            # Ziele muessen strikt monoton bleiben, sonst ist TP2 < TP1 moeglich.
            if targets:
                previous = targets[-1]
                if is_long and adjusted <= previous:
                    adjusted = raw
                if not is_long and adjusted >= previous:
                    adjusted = raw

            targets.append(adjusted)

        return targets[0], targets[1], targets[2]

    @staticmethod
    def position_size_for_risk(risk_amount: float, stop_distance: float) -> float:
        """Stueckzahl, bei der ein Stop-Treffer genau ``risk_amount`` kostet."""
        if stop_distance <= 0 or risk_amount <= 0:
            return 0.0
        return risk_amount / stop_distance

    def _position_size(self, stop_distance: float) -> float:
        """Informative Positionsgroesse bezogen auf das Referenzkapital.

        Es wird kein Kontostand abgefragt und keine Order erzeugt.
        """
        risk_amount = self._config.reference_capital * self._config.max_risk_percent / 100.0
        return self.position_size_for_risk(risk_amount, stop_distance)

    @staticmethod
    def _invalidation_note(stop_loss: float, timeframe: str, *, is_long: bool) -> str:
        """Invalidierung auf Schlusskursbasis — nicht auf ein kurzes Durchstechen."""
        relation = "unter" if is_long else "ueber"
        return f"{timeframe}-Schlusskurs {relation} {stop_loss:.6g}"
