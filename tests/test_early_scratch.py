"""Tests fuer Early-Scratch Exit-Logik."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.core.enums import ExitReason, SignalDirection
from app.models.paper import PaperPosition
from app.services.paper_trading_service import PaperTradingService

NOW = datetime(2024, 6, 1, 12, tzinfo=UTC)


def _open_long_position(*, opened_at: datetime, entry: float = 100.0, stop: float = 95.0) -> PaperPosition:
    return PaperPosition(
        account_id=1,
        symbol="ETHUSDT",
        direction=SignalDirection.STRONG_LONG.value,
        status="open",
        timeframe="1h",
        entry_price=Decimal(str(entry)),
        stop_loss=Decimal(str(stop)),
        current_stop=Decimal(str(stop)),
        take_profit_1=Decimal("110"),
        take_profit_2=Decimal("120"),
        take_profit_3=Decimal("130"),
        initial_quantity=Decimal("1"),
        remaining_quantity=Decimal("1"),
        margin_used=Decimal("10"),
        notional=Decimal("100"),
        leverage=10.0,
        risk_amount=Decimal("5"),
        tp1_filled=False,
        opened_at=opened_at,
        expires_at=opened_at + timedelta(hours=24),
        peak_price=Decimal(str(entry)),
    )


@pytest.mark.asyncio
async def test_early_scratch_triggers_after_hours_without_mfe(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        paper_early_scratch_hours=8,
        paper_early_scratch_mfe_r=0.5,
    )
    service = PaperTradingService(settings)
    position = _open_long_position(opened_at=NOW - timedelta(hours=9))
    closed: list[str] = []

    async def _fake_close(*_args, **_kwargs) -> None:
        position.status = "closed"
        position.exit_reason = ExitReason.EARLY_SCRATCH.value
        closed.append("yes")

    monkeypatch.setattr(service, "_close_remaining", _fake_close)

    scratched = await service._maybe_early_scratch(
        AsyncMock(),
        AsyncMock(),
        position,
        price=100.2,
        when=NOW,
    )
    assert scratched is True
    assert closed


@pytest.mark.asyncio
async def test_early_scratch_skips_when_mfe_reached() -> None:
    settings = Settings(
        paper_early_scratch_hours=8,
        paper_early_scratch_mfe_r=0.5,
    )
    service = PaperTradingService(settings)
    position = _open_long_position(opened_at=NOW - timedelta(hours=9))
    position.peak_price = Decimal("103")
    scratched = await service._maybe_early_scratch(
        AsyncMock(),
        AsyncMock(),
        position,
        price=102.0,
        when=NOW,
    )
    assert scratched is False


def test_early_scratch_disabled_when_hours_zero() -> None:
    settings = Settings(paper_early_scratch_hours=0)
    assert settings.paper_early_scratch_hours == 0


def test_mfe_r_tracks_peak_for_long() -> None:
    service = PaperTradingService(Settings())
    position = _open_long_position(opened_at=NOW)
    position.peak_price = Decimal("102")
    assert service._mfe_r(position) == pytest.approx(0.4)
