"""Signal-Deduplizierung: Fingerprint, Cooldown und Relevanzpruefung.

Zweistufig, damit ein Redis-Ausfall keine Signalflut ausloest: Stufe 1 vergleicht
Fingerprints, Stufe 2 prueft, ob sich ein Signal innerhalb des Cooldowns
*relevant* veraendert hat. Ohne Redis wird auf eine In-Memory-Sperre
zurueckgefallen und das letzte bekannte Signal aus der Datenbank verglichen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.core.enums import SignalDirection, SuppressionReason
from app.core.logging import get_logger
from app.core.time import utc_now
from app.signals.types import SignalResult

logger = get_logger(__name__)

COOLDOWN_KEY_PREFIX = "signal:cooldown"

#: Score-Aenderung, ab der ein Signal innerhalb des Cooldowns als neu gilt.
RELEVANT_SCORE_DELTA = 10.0

#: Verschiebung des Entry-Mittelpunkts in ATR, ab der ein Signal als neu gilt.
RELEVANT_ENTRY_ATR_SHIFT = 0.75


@dataclass(frozen=True)
class PreviousSignal:
    """Der zuletzt versendete Zustand eines Symbols und Timeframes."""

    fingerprint: str
    direction: SignalDirection
    score: float
    entry_mid: float
    created_at: datetime


@dataclass(frozen=True)
class DedupDecision:
    """Ergebnis der Duplikatpruefung."""

    should_send: bool
    reason: SuppressionReason | None = None
    detail: str = ""


class SignalHistoryReader(Protocol):
    """Minimales Interface, um das letzte Signal zu lesen.

    Bewusst schmal gehalten: der Deduplizierer soll nicht das gesamte
    Repository kennen.
    """

    async def get_last_dispatched(self, symbol: str, timeframe: str) -> PreviousSignal | None: ...


class SignalDeduplicator:
    """Entscheidet, ob ein Signal versendet werden darf."""

    def __init__(
        self,
        *,
        cooldown_minutes: int = 120,
        redis_client: Any | None = None,
        history_reader: SignalHistoryReader | None = None,
    ) -> None:
        self._cooldown_minutes = max(0, cooldown_minutes)
        self._redis = redis_client
        self._history = history_reader
        #: Fallback, wenn Redis nicht erreichbar ist.
        self._memory: dict[str, PreviousSignal] = {}

    async def evaluate(
        self,
        result: SignalResult,
        *,
        min_score: float,
        min_risk_reward_ratio: float,
        min_data_quality: float = 60.0,
        require_strong: bool = False,
        short_max_score: float | None = None,
        now: datetime | None = None,
    ) -> DedupDecision:
        """Alle Versandbedingungen pruefen."""
        reference_time = now or utc_now()

        if not result.direction.is_actionable:
            return DedupDecision(
                False,
                SuppressionReason.NOT_ACTIONABLE,
                result.no_trade_reason or f"Richtung {result.direction.value}",
            )

        if require_strong and result.direction not in {
            SignalDirection.STRONG_LONG,
            SignalDirection.STRONG_SHORT,
        }:
            return DedupDecision(
                False,
                SuppressionReason.NOT_STRONG,
                f"Nur STRONG-Signale erlaubt, erhalten: {result.direction.value}",
            )

        if result.expires_at <= reference_time:
            return DedupDecision(False, SuppressionReason.EXPIRED, "Signal ist bereits abgelaufen")

        if result.direction.is_long and result.score < min_score:
            return DedupDecision(
                False,
                SuppressionReason.BELOW_MIN_SCORE,
                f"Score {result.score:.1f} unter dem Minimum von {min_score:.1f}",
            )

        short_max = (
            short_max_score if short_max_score is not None else max(0.0, 100.0 - min_score)
        )
        if result.direction.is_short and result.score > short_max:
            return DedupDecision(
                False,
                SuppressionReason.BELOW_MIN_SCORE,
                f"Short-Score {result.score:.1f} ueber dem Maximum von {short_max:.1f}",
            )

        if result.data_quality < min_data_quality:
            return DedupDecision(
                False,
                SuppressionReason.LOW_DATA_QUALITY,
                f"Datenqualitaet {result.data_quality:.0f} zu niedrig",
            )

        ratio = result.risk.risk_reward_ratio if result.risk else 0.0
        if ratio < min_risk_reward_ratio:
            return DedupDecision(
                False,
                SuppressionReason.RISK_REWARD_TOO_LOW,
                f"Chance-Risiko-Verhaeltnis {ratio:.2f} unter "
                f"dem Minimum von {min_risk_reward_ratio:.2f}",
            )

        previous = await self._load_previous(result.symbol, result.primary_timeframe)
        if previous is None:
            return DedupDecision(True)

        if previous.fingerprint == result.fingerprint:
            return DedupDecision(
                False, SuppressionReason.DUPLICATE, "Identisches Signal bereits versendet"
            )

        elapsed_minutes = (reference_time - previous.created_at).total_seconds() / 60.0
        if elapsed_minutes >= self._cooldown_minutes:
            return DedupDecision(True)

        changed, detail = self._is_relevant_change(result, previous)
        if changed:
            return DedupDecision(True, None, detail)

        return DedupDecision(
            False,
            SuppressionReason.COOLDOWN,
            f"Cooldown aktiv ({elapsed_minutes:.0f} von {self._cooldown_minutes} Minuten), "
            f"keine relevante Aenderung",
        )

    async def record_dispatch(self, result: SignalResult) -> None:
        """Versandzustand fuer nachfolgende Pruefungen merken."""
        entry_mid = result.risk.entry_mid if result.risk else result.reference_price
        record = PreviousSignal(
            fingerprint=result.fingerprint,
            direction=result.direction,
            score=result.score,
            entry_mid=entry_mid,
            created_at=result.created_at,
        )
        key = self._key(result.symbol, result.primary_timeframe)
        self._memory[key] = record

        if self._redis is None:
            return
        try:
            await self._redis.hset(  # type: ignore[misc]
                key,
                mapping={
                    "fingerprint": record.fingerprint,
                    "direction": record.direction.value,
                    "score": str(record.score),
                    "entry_mid": str(record.entry_mid),
                    "created_at": record.created_at.isoformat(),
                },
            )
            # Ablauf mit Puffer, damit der Vergleich den Cooldown ueberdauert.
            await self._redis.expire(key, max(60, self._cooldown_minutes * 60 * 2))
        except Exception as exc:
            logger.warning("dedup_redis_write_failed", key=key, error=str(exc))

    # --- interne Helfer ---------------------------------------------------

    def _is_relevant_change(
        self, result: SignalResult, previous: PreviousSignal
    ) -> tuple[bool, str]:
        if result.direction != previous.direction:
            return True, (
                f"Richtungswechsel von {previous.direction.value} zu {result.direction.value}"
            )

        if abs(result.score - previous.score) >= RELEVANT_SCORE_DELTA:
            return True, (
                f"Score hat sich von {previous.score:.1f} auf {result.score:.1f} geaendert"
            )

        atr_value = self._primary_atr(result)
        if atr_value and atr_value > 0 and result.risk is not None:
            shift = abs(result.risk.entry_mid - previous.entry_mid) / atr_value
            if shift >= RELEVANT_ENTRY_ATR_SHIFT:
                return True, f"Entry-Bereich um {shift:.2f} ATR verschoben"

        return False, ""

    @staticmethod
    def _primary_atr(result: SignalResult) -> float | None:
        assessment = result.assessments.get(result.primary_timeframe)
        return assessment.indicators.atr_14 if assessment else None

    async def _load_previous(self, symbol: str, timeframe: str) -> PreviousSignal | None:
        key = self._key(symbol, timeframe)

        if self._redis is not None:
            try:
                data = await self._redis.hgetall(key)
                if data:
                    return PreviousSignal(
                        fingerprint=str(data["fingerprint"]),
                        direction=SignalDirection(str(data["direction"])),
                        score=float(data["score"]),
                        entry_mid=float(data["entry_mid"]),
                        created_at=datetime.fromisoformat(str(data["created_at"])),
                    )
            except Exception as exc:
                logger.warning("dedup_redis_read_failed", key=key, error=str(exc))

        if key in self._memory:
            return self._memory[key]

        # Letzte Instanz: Datenbank. Verhindert eine Signalflut nach Redis-Neustart.
        if self._history is not None:
            try:
                return await self._history.get_last_dispatched(symbol, timeframe)
            except Exception as exc:
                logger.warning("dedup_history_read_failed", symbol=symbol, error=str(exc))

        return None

    @staticmethod
    def _key(symbol: str, timeframe: str) -> str:
        return f"{COOLDOWN_KEY_PREFIX}:{symbol.upper()}:{timeframe}"
