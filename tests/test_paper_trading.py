"""Tests fuer Paper-Trading und Short-Score-Gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.enums import ExitReason, SignalDirection, SuppressionReason
from app.repositories.asset_repository import AssetRepository
from app.repositories.paper_repository import PaperRepository
from app.services.analysis_service import AnalysisOutcome
from app.services.paper_trading_service import PaperTradingService
from app.signals.dedup import SignalDeduplicator
from app.signals.types import RiskParameters
from tests.test_dedup import make_result


NOW = datetime(2024, 6, 1, 12, tzinfo=UTC)


def _tight_long_risk(entry: float = 100.0) -> RiskParameters:
    return RiskParameters(
        entry_low=entry - 1.0,
        entry_high=entry + 1.0,
        stop_loss=entry - 5.0,
        take_profit_1=entry + 5.0,
        take_profit_2=entry + 10.0,
        take_profit_3=entry + 15.0,
        risk_reward_ratio=3.0,
        risk_percent=1.0,
        suggested_position_size=0.1,
        stop_distance_percent=5.0,
        invalidation_note="test",
    )


class TestShortScoreGate:
    @pytest.mark.asyncio
    async def test_allows_strong_short_with_low_score(self) -> None:
        decision = await SignalDeduplicator().evaluate(
            make_result(
                direction=SignalDirection.STRONG_SHORT,
                score=18.0,
                fingerprint="short-ok",
            ),
            min_score=75.0,
            short_max_score=25.0,
            min_risk_reward_ratio=2.0,
            require_strong=True,
            now=NOW,
        )
        assert decision.should_send is True

    @pytest.mark.asyncio
    async def test_rejects_short_above_max_score(self) -> None:
        decision = await SignalDeduplicator().evaluate(
            make_result(
                direction=SignalDirection.STRONG_SHORT,
                score=40.0,
                fingerprint="short-high",
            ),
            min_score=75.0,
            short_max_score=25.0,
            min_risk_reward_ratio=2.0,
            require_strong=True,
            now=NOW,
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.BELOW_MIN_SCORE


class TestUniverseMaxRank:
    @pytest.mark.asyncio
    async def test_list_universe_batch_respects_max_rank(self, session: AsyncSession) -> None:
        repo = AssetRepository(session)
        for symbol, rank in (("AAAUSDT", 50), ("BBBUSDT", 150), ("CCCUSDT", 10)):
            await repo.upsert_universe_entry(
                symbol=symbol,
                base_asset=symbol[:3],
                quote_asset="USDT",
                exchange="stub",
                coingecko_id=symbol.lower(),
                market_cap_rank=rank,
                market_cap_usd=Decimal("1"),
            )

        batch = await repo.list_universe_batch(10, max_rank=100)
        symbols = {asset.symbol for asset in batch}
        assert "AAAUSDT" in symbols
        assert "CCCUSDT" in symbols
        assert "BBBUSDT" not in symbols


class TestPaperTrading:
    @pytest.mark.asyncio
    async def test_open_and_scale_out_to_breakeven(self, session: AsyncSession) -> None:
        settings = Settings(
            enable_paper_trading=True,
            paper_initial_balance=2000.0,
            paper_margin_per_trade=100.0,
            paper_leverage=5.0,
            paper_fee_percent=0.0,
            paper_move_stop_to_breakeven=True,
            paper_retest_entry_enabled=False,
        )
        service = PaperTradingService(settings)
        result = make_result(
            direction=SignalDirection.STRONG_LONG,
            score=80.0,
            entry_mid=100.0,
            fingerprint="paper-1",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        result.risk = _tight_long_risk(100.0)
        outcome = AnalysisOutcome(result=result, price_precision=2)

        position = await service.open_from_signal(session, outcome)
        assert position is not None
        assert float(position.notional) == pytest.approx(500.0)
        assert float(position.remaining_quantity) == pytest.approx(5.0)

        account = await service.get_or_create_account(session)
        assert float(account.cash_balance) == pytest.approx(1900.0)

        updated = await service.update_open_positions(session, {"BTCUSDT": 105.0})
        assert len(updated) == 1
        assert position.tp1_filled is True
        assert float(position.current_stop) == pytest.approx(100.0)
        assert float(position.remaining_quantity) < 5.0

        await service.update_open_positions(session, {"BTCUSDT": 99.0})
        assert position.status == "closed"
        assert position.exit_reason == ExitReason.STOP_LOSS.value

        summary = await service.summary(session)
        assert summary.closed_trades == 1
        assert summary.open_positions == 0

    @pytest.mark.asyncio
    async def test_retest_opens_as_pending_without_cash_lock(self, session: AsyncSession) -> None:
        settings = Settings(
            enable_paper_trading=True,
            paper_initial_balance=2000.0,
            paper_margin_per_trade=100.0,
            paper_leverage=5.0,
            paper_fee_percent=0.0,
            paper_retest_entry_enabled=True,
        )
        service = PaperTradingService(settings)
        result = make_result(
            direction=SignalDirection.STRONG_LONG,
            score=80.0,
            entry_mid=100.0,
            fingerprint="paper-retest",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        result.risk = _tight_long_risk(100.0)
        outcome = AnalysisOutcome(result=result, price_precision=2)

        position = await service.open_from_signal(session, outcome)
        assert position is not None
        assert position.status == "pending"
        account = await service.get_or_create_account(session)
        assert float(account.cash_balance) == pytest.approx(2000.0)
        assert float(position.margin_used) == pytest.approx(0.0)

        second = await service.open_from_signal(session, outcome)
        assert second is None

    @pytest.mark.asyncio
    async def test_skips_duplicate_open_symbol(self, session: AsyncSession) -> None:
        settings = Settings(
            enable_paper_trading=True,
            paper_initial_balance=2000.0,
            paper_margin_per_trade=100.0,
            paper_leverage=5.0,
            paper_fee_percent=0.0,
            paper_retest_entry_enabled=False,
        )
        service = PaperTradingService(settings)
        outcome = AnalysisOutcome(
            result=make_result(direction=SignalDirection.LONG, score=80.0, entry_mid=50.0),
            price_precision=2,
        )
        first = await service.open_from_signal(session, outcome)
        second = await service.open_from_signal(session, outcome)
        assert first is not None
        assert second is None

        opens = await PaperRepository(session).list_open_positions(first.account_id)
        assert len(opens) == 1

    @pytest.mark.asyncio
    async def test_notifies_on_open_and_close(self, session: AsyncSession) -> None:
        events: list[tuple[str, str]] = []

        class RecordingNotifier:
            async def notify_open(
                self, position, *, retest_fill: bool = False, reasons=None
            ) -> None:
                events.append(("open", position.symbol))

            async def notify_close(self, position) -> None:
                events.append(("close", position.symbol))

        settings = Settings(
            enable_paper_trading=True,
            paper_initial_balance=2000.0,
            paper_margin_per_trade=100.0,
            paper_leverage=5.0,
            paper_fee_percent=0.0,
            paper_move_stop_to_breakeven=True,
            paper_retest_entry_enabled=False,
        )
        service = PaperTradingService(settings, notifier=RecordingNotifier())
        result = make_result(
            direction=SignalDirection.STRONG_LONG,
            score=80.0,
            entry_mid=100.0,
            fingerprint="paper-notify",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        result.risk = _tight_long_risk(100.0)
        outcome = AnalysisOutcome(result=result, price_precision=2)

        position = await service.open_from_signal(session, outcome)
        assert position is not None
        await service.update_open_positions(session, {"BTCUSDT": 105.0})
        await service.update_open_positions(session, {"BTCUSDT": 99.0})

        assert events == [("open", "BTCUSDT"), ("close", "BTCUSDT")]
