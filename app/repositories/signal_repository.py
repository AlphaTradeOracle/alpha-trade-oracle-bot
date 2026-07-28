"""Datenzugriff fuer Signale, Score-Komponenten, Zustellungen und LLM-Protokolle."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DeliveryStatus, SignalDirection, SuppressionReason
from app.core.time import utc_now
from app.llm.schemas import LLMCallResult
from app.models.market import Asset
from app.models.signal import LLMRequest, Signal, SignalDelivery, SignalScoreComponent
from app.signals.dedup import PreviousSignal
from app.signals.types import SignalResult


class SignalRepository:
    """Persistierung und Auswertung von Signalen."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        result: SignalResult,
        asset_id: int,
        *,
        strategy_version_id: int | None = None,
        llm_summary: str | None = None,
    ) -> Signal:
        """Signal samt vollstaendigem Score-Breakdown speichern."""
        risk = result.risk
        signal = Signal(
            asset_id=asset_id,
            strategy_version_id=strategy_version_id,
            expires_at=result.expires_at,
            direction=result.direction.value,
            analyzed_timeframes=",".join(result.analyzed_timeframes),
            primary_timeframe=result.primary_timeframe,
            market_phase=result.market_phase.value,
            score=result.score,
            confidence=result.confidence.value,
            reference_price=_dec(result.reference_price),
            entry_low=_dec(risk.entry_low) if risk else None,
            entry_high=_dec(risk.entry_high) if risk else None,
            stop_loss=_dec(risk.stop_loss) if risk else None,
            take_profit_1=_dec(risk.take_profit_1) if risk else None,
            take_profit_2=_dec(risk.take_profit_2) if risk else None,
            take_profit_3=_dec(risk.take_profit_3) if risk else None,
            risk_reward_ratio=risk.risk_reward_ratio if risk else None,
            risk_percent=risk.risk_percent if risk else None,
            suggested_position_size=_dec(risk.suggested_position_size) if risk else None,
            data_quality=result.data_quality,
            invalidation_note=risk.invalidation_note if risk else result.no_trade_reason,
            reasons=result.reasons,
            counter_arguments=result.counter_arguments,
            indicators_used=result.indicators_used,
            llm_summary=llm_summary,
            fingerprint=result.fingerprint,
            is_dispatched=False,
        )

        signal.score_components = [
            SignalScoreComponent(
                category=component.category.value,
                raw_score=round(component.raw_score, 2),
                weight=round(component.weight, 4),
                weighted_score=round(component.weighted_score, 4),
                detail=component.detail[:2000] if component.detail else None,
            )
            for component in result.components
        ]

        self._session.add(signal)
        await self._session.flush()
        return signal

    async def mark_dispatched(self, signal_id: int) -> None:
        signal = await self._session.get(Signal, signal_id)
        if signal is not None:
            signal.is_dispatched = True

    async def record_delivery(
        self,
        signal_id: int,
        telegram_chat_id: int,
        *,
        status: DeliveryStatus,
        message_id: int | None = None,
        suppression_reason: SuppressionReason | None = None,
        error_message: str | None = None,
    ) -> SignalDelivery:
        """Zustellversuch protokollieren — auch Unterdrueckungen."""
        delivery = SignalDelivery(
            signal_id=signal_id,
            telegram_chat_id=telegram_chat_id,
            status=status.value,
            message_id=message_id,
            suppression_reason=suppression_reason.value if suppression_reason else None,
            error_message=error_message[:2000] if error_message else None,
            sent_at=utc_now() if status == DeliveryStatus.SENT else None,
            created_at=utc_now(),
        )
        self._session.add(delivery)
        await self._session.flush()
        return delivery

    async def record_llm_request(
        self, call: LLMCallResult, *, signal_id: int | None = None
    ) -> LLMRequest:
        """Tokenverbrauch, Laufzeit und Validierungsstatus protokollieren."""
        request = LLMRequest(
            signal_id=signal_id,
            provider=call.provider,
            model=call.model,
            prompt_version=call.prompt_version,
            status=call.status,
            prompt_tokens=call.usage.prompt_tokens,
            completion_tokens=call.usage.completion_tokens,
            total_tokens=call.usage.total_tokens,
            duration_ms=call.duration_ms,
            validation_error=call.validation_error,
            error_message=call.error_message,
            extra={"attempts": call.attempts},
            created_at=utc_now(),
        )
        self._session.add(request)
        await self._session.flush()
        return request

    async def get_by_id(self, signal_id: int) -> Signal | None:
        return await self._session.get(Signal, signal_id)

    async def list_recent(
        self,
        *,
        symbol: str | None = None,
        direction: SignalDirection | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Signal]:
        statement = select(Signal).order_by(Signal.created_at.desc())
        if symbol:
            statement = statement.join(Asset).where(Asset.symbol == symbol.upper())
        if direction:
            statement = statement.where(Signal.direction == direction.value)
        result = await self._session.execute(statement.limit(limit).offset(offset))
        return list(result.scalars())

    async def list_since(
        self,
        since: datetime,
        *,
        actionable_only: bool = True,
        dispatched_only: bool = False,
        limit: int = 500,
    ) -> list[Signal]:
        """Signale ab einem Zeitpunkt laden (fuer Paper-Backfill)."""
        statement = select(Signal).where(Signal.created_at >= since)
        if actionable_only:
            statement = statement.where(
                Signal.direction.in_(
                    [
                        SignalDirection.STRONG_LONG.value,
                        SignalDirection.LONG.value,
                        SignalDirection.SHORT.value,
                        SignalDirection.STRONG_SHORT.value,
                    ]
                )
            )
        if dispatched_only:
            statement = statement.where(Signal.is_dispatched.is_(True))
        statement = statement.order_by(Signal.created_at.asc()).limit(limit)
        result = await self._session.execute(statement)
        return list(result.scalars())

    async def get_last_dispatched(self, symbol: str, timeframe: str) -> PreviousSignal | None:
        """Letztes versendetes Signal — Fallback der Deduplizierung ohne Redis."""
        result = await self._session.execute(
            select(Signal)
            .join(Asset)
            .where(
                Asset.symbol == symbol.upper(),
                Signal.primary_timeframe == timeframe,
                Signal.is_dispatched.is_(True),
            )
            .order_by(Signal.created_at.desc())
            .limit(1)
        )
        signal = result.scalar_one_or_none()
        if signal is None:
            return None

        entry_mid = _entry_mid(signal)
        return PreviousSignal(
            fingerprint=signal.fingerprint,
            direction=SignalDirection(signal.direction),
            score=float(signal.score),
            entry_mid=entry_mid,
            created_at=signal.created_at,
        )

    async def performance_summary(self, *, days: int = 30) -> dict[str, float | int]:
        """Aggregierte Kennzahlen der erzeugten Signale.

        Bewertet die Signalproduktion, nicht die Handelsperformance — dafuer
        muesste der Ausgang jedes Signals nachverfolgt werden, was auf der
        Roadmap steht.
        """
        since = utc_now() - timedelta(days=days)

        totals = await self._session.execute(
            select(
                func.count(Signal.id),
                func.avg(Signal.score),
                func.avg(Signal.risk_reward_ratio),
                func.avg(Signal.data_quality),
            ).where(Signal.created_at >= since)
        )
        total, avg_score, avg_rr, avg_quality = totals.one()

        dispatched = await self._session.execute(
            select(func.count(Signal.id)).where(
                Signal.created_at >= since, Signal.is_dispatched.is_(True)
            )
        )

        by_direction = await self._session.execute(
            select(Signal.direction, func.count(Signal.id))
            .where(Signal.created_at >= since)
            .group_by(Signal.direction)
        )

        summary: dict[str, float | int] = {
            "period_days": days,
            "signals_total": int(total or 0),
            "signals_dispatched": int(dispatched.scalar_one() or 0),
            "average_score": round(float(avg_score), 2) if avg_score is not None else 0.0,
            "average_risk_reward": round(float(avg_rr), 2) if avg_rr is not None else 0.0,
            "average_data_quality": (
                round(float(avg_quality), 2) if avg_quality is not None else 0.0
            ),
        }
        for direction, count in by_direction:
            summary[f"count_{direction.lower()}"] = int(count)
        return summary


def _dec(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _entry_mid(signal: Signal) -> float:
    if signal.entry_low is not None and signal.entry_high is not None:
        return float(signal.entry_low + signal.entry_high) / 2.0
    return float(signal.reference_price)
