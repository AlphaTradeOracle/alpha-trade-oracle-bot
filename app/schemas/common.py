"""Gemeinsame API-Schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

#: Pflichthinweis in jeder API-Antwort, die eine Analyse enthaelt.
DISCLAIMER_TEXT = (
    "Diese Ausgabe ist keine Finanzberatung. Kryptowaehrungen sind hochriskant. "
    "Es werden keine Gewinne zugesagt und keine Orders ausgefuehrt."
)


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    app: str
    version: str
    environment: str
    timestamp: datetime


class ReadinessResponse(BaseModel):
    status: str = Field(examples=["ready", "not_ready"])
    checked_at: datetime
    components: dict[str, dict[str, Any]]


class VersionResponse(BaseModel):
    app: str
    version: str
    environment: str
    market_data_provider: str
    llm_enabled: bool
    sentiment_enabled: bool
    backtesting_enabled: bool


class ErrorResponse(BaseModel):
    detail: str
    error_type: str | None = None


class AssetResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    symbol: str
    base_asset: str
    quote_asset: str
    exchange: str
    price_precision: int
    is_active: bool
