"""API-Schemas fuer Signale und Analysen."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import DISCLAIMER_TEXT
from app.signals.types import SignalResult


class ScoreComponentResponse(BaseModel):
    category: str
    raw_score: float = Field(description="Rohwert in [-100, +100]; positiv ist bullisch.")
    weight: float
    weighted_score: float
    detail: str | None = None


class RiskResponse(BaseModel):
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    risk_reward_ratio: float
    stop_distance_percent: float
    risk_percent: float
    suggested_position_size: float = Field(
        description="Rein informativ. Es werden keine Orders erzeugt."
    )
    invalidation_note: str
    warnings: list[str] = Field(default_factory=list)


class SignalResponse(BaseModel):
    """Vollstaendiges Signal."""

    model_config = {"from_attributes": True}

    id: int | None = None
    symbol: str
    created_at: datetime
    expires_at: datetime
    direction: str
    score: float = Field(ge=0.0, le=100.0)
    confidence: str
    market_phase: str
    primary_timeframe: str
    analyzed_timeframes: list[str]
    reference_price: float
    data_quality: float
    risk: RiskResponse | None = None
    score_components: list[ScoreComponentResponse] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    counter_arguments: list[str] = Field(default_factory=list)
    indicators_used: list[str] = Field(default_factory=list)
    llm_summary: str | None = None
    no_trade_reason: str | None = None
    is_dispatched: bool = False
    disclaimer: str = DISCLAIMER_TEXT

    @classmethod
    def from_result(cls, result: SignalResult, *, signal_id: int | None = None) -> SignalResponse:
        return cls(
            id=signal_id,
            symbol=result.symbol,
            created_at=result.created_at,
            expires_at=result.expires_at,
            direction=result.direction.value,
            score=result.score,
            confidence=result.confidence.value,
            market_phase=result.market_phase.value,
            primary_timeframe=result.primary_timeframe,
            analyzed_timeframes=result.analyzed_timeframes,
            reference_price=result.reference_price,
            data_quality=result.data_quality,
            risk=(
                RiskResponse(
                    entry_low=result.risk.entry_low,
                    entry_high=result.risk.entry_high,
                    stop_loss=result.risk.stop_loss,
                    take_profit_1=result.risk.take_profit_1,
                    take_profit_2=result.risk.take_profit_2,
                    take_profit_3=result.risk.take_profit_3,
                    risk_reward_ratio=result.risk.risk_reward_ratio,
                    stop_distance_percent=result.risk.stop_distance_percent,
                    risk_percent=result.risk.risk_percent,
                    suggested_position_size=result.risk.suggested_position_size,
                    invalidation_note=result.risk.invalidation_note,
                    warnings=result.risk.warnings,
                )
                if result.risk is not None
                else None
            ),
            score_components=[
                ScoreComponentResponse(
                    category=component.category.value,
                    raw_score=round(component.raw_score, 2),
                    weight=round(component.weight, 4),
                    weighted_score=round(component.weighted_score, 4),
                    detail=component.detail or None,
                )
                for component in result.components
            ],
            reasons=result.reasons,
            counter_arguments=result.counter_arguments,
            indicators_used=result.indicators_used,
            llm_summary=result.llm_summary,
            no_trade_reason=result.no_trade_reason,
        )

    @classmethod
    def from_orm_signal(cls, signal: object, symbol: str) -> SignalResponse:
        """Aus einem persistierten Signal aufbauen."""
        risk = None
        if _get(signal, "stop_loss") is not None:
            risk = RiskResponse(
                entry_low=_float(_get(signal, "entry_low")),
                entry_high=_float(_get(signal, "entry_high")),
                stop_loss=_float(_get(signal, "stop_loss")),
                take_profit_1=_float(_get(signal, "take_profit_1")),
                take_profit_2=_float(_get(signal, "take_profit_2")),
                take_profit_3=_float(_get(signal, "take_profit_3")),
                risk_reward_ratio=_float(_get(signal, "risk_reward_ratio")),
                stop_distance_percent=0.0,
                risk_percent=_float(_get(signal, "risk_percent")),
                suggested_position_size=_float(_get(signal, "suggested_position_size")),
                invalidation_note=_get(signal, "invalidation_note") or "",
            )

        raw_components = _get(signal, "score_components")
        components: list[Any] = raw_components if isinstance(raw_components, list) else []
        return cls(
            id=_get(signal, "id"),  # type: ignore[arg-type]
            symbol=symbol,
            created_at=_get(signal, "created_at"),  # type: ignore[arg-type]
            expires_at=_get(signal, "expires_at"),  # type: ignore[arg-type]
            direction=_get(signal, "direction"),  # type: ignore[arg-type]
            score=_float(_get(signal, "score")),
            confidence=_get(signal, "confidence"),  # type: ignore[arg-type]
            market_phase=_get(signal, "market_phase"),  # type: ignore[arg-type]
            primary_timeframe=_get(signal, "primary_timeframe"),  # type: ignore[arg-type]
            analyzed_timeframes=str(_get(signal, "analyzed_timeframes") or "").split(","),
            reference_price=_float(_get(signal, "reference_price")),
            data_quality=_float(_get(signal, "data_quality")),
            risk=risk,
            score_components=[
                ScoreComponentResponse(
                    category=component.category,
                    raw_score=_float(component.raw_score),
                    weight=_float(component.weight),
                    weighted_score=_float(component.weighted_score),
                    detail=component.detail,
                )
                for component in components
            ],
            reasons=_get(signal, "reasons") or [],  # type: ignore[arg-type]
            counter_arguments=_get(signal, "counter_arguments") or [],  # type: ignore[arg-type]
            indicators_used=_get(signal, "indicators_used") or [],  # type: ignore[arg-type]
            llm_summary=_get(signal, "llm_summary"),  # type: ignore[arg-type]
            is_dispatched=bool(_get(signal, "is_dispatched")),
        )


class AnalysisRequest(BaseModel):
    """Anfrage fuer eine Ad-hoc-Analyse."""

    symbol: str = Field(min_length=3, max_length=32, examples=["BTCUSDT"])
    timeframes: list[str] | None = Field(
        default=None, description="Abweichende Timeframes, sonst die Konfiguration."
    )
    use_llm: bool | None = Field(
        default=None, description="Ueberschreibt ENABLE_LLM_ANALYSIS fuer diese Anfrage."
    )
    persist: bool = Field(default=True, description="Ergebnis in der Datenbank speichern.")


class AnalysisResponse(BaseModel):
    signal: SignalResponse
    skipped_timeframes: list[str] = Field(default_factory=list)
    llm_used: bool = False
    llm_status: str | None = None
    disclaimer: str = DISCLAIMER_TEXT


class PerformanceResponse(BaseModel):
    period_days: int
    signals_total: int
    signals_dispatched: int
    average_score: float
    average_risk_reward: float
    average_data_quality: float
    by_direction: dict[str, int] = Field(default_factory=dict)
    note: str = (
        "Die Auswertung bezieht sich auf die Signalproduktion, nicht auf realisierte "
        "Handelsergebnisse."
    )
    disclaimer: str = DISCLAIMER_TEXT


def _get(obj: object, name: str) -> object:
    return getattr(obj, name, None)


def _float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)  # type: ignore[arg-type]
