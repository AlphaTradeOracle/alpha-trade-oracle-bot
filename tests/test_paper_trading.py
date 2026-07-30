"""Tests fuer Paper-Trading und Short-Score-Gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.enums import ExitReason, SignalDirection, SuppressionReason
from app.core.time import ensure_utc
from app.market_data.types import Candle, CandleSeries
from app.repositories.asset_repository import AssetRepository
from app.repositories.paper_repository import PaperRepository
from app.services.analysis_service import AnalysisOutcome
from app.services.paper_trading_service import PaperTradingService
from app.signals.dedup import SignalDeduplicator
from app.signals.retest_entry import arm_retest_entry
from app.signals.types import RiskParameters
from tests.test_dedup import make_result

NOW = datetime(2024, 6, 1, 12, tzinfo=UTC)


def _long_risk(entry: float = 100.0, stop_distance: float = 5.0) -> RiskParameters:
    return RiskParameters(
        entry_low=entry - 1.0,
        entry_high=entry + 1.0,
        stop_loss=entry - stop_distance,
        take_profit_1=entry + stop_distance,
        take_profit_2=entry + 2 * stop_distance,
        take_profit_3=entry + 3 * stop_distance,
        risk_reward_ratio=3.0,
        risk_percent=1.0,
        suggested_position_size=0.1,
        stop_distance_percent=stop_distance / entry * 100.0,
        invalidation_note="test",
    )


def _tight_long_risk(entry: float = 100.0) -> RiskParameters:
    return _long_risk(entry, 5.0)


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


class _StubCandleProvider:
    """Liefert eine feste Kerzenreihe fuer die Retest-Aufloesung."""

    name = "stub"

    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    async def get_candles(self, symbol, timeframe, **kwargs) -> CandleSeries:
        return CandleSeries(symbol=symbol, timeframe=timeframe, candles=self._candles)


def _flat_candles(start: datetime, count: int, base: float = 100.0) -> list[Candle]:
    return [
        Candle(
            open_time=start + timedelta(hours=i),
            close_time=start + timedelta(hours=i + 1),
            open=base,
            high=base + 1.0,
            low=base - 1.0,
            close=base,
            volume=1000.0,
        )
        for i in range(count)
    ]


class TestRetestFill:
    @pytest.mark.asyncio
    async def test_expiry_window_starts_at_fill_not_at_arm(
        self, session: AsyncSession
    ) -> None:
        settings = Settings(
            enable_paper_trading=True,
            paper_initial_balance=5000.0,
            paper_leverage=10.0,
            paper_risk_per_trade_usd=50.0,
            paper_fee_percent=0.0,
            paper_retest_entry_enabled=True,
            signal_expiry_multiplier=24,
        )
        service = PaperTradingService(settings)
        armed_at = datetime(2024, 6, 1, 12, tzinfo=UTC)
        result = make_result(
            direction=SignalDirection.STRONG_LONG,
            score=80.0,
            entry_mid=100.0,
            fingerprint="paper-retest-expiry",
            created_at=armed_at,
        )
        result.risk = _tight_long_risk(100.0)
        outcome = AnalysisOutcome(result=result, price_precision=2)

        position = await service.open_from_signal(session, outcome, opened_at=armed_at)
        assert position is not None and position.status == "pending"

        candles = _flat_candles(armed_at - timedelta(hours=30), 31)
        pullback_time = armed_at + timedelta(hours=2)
        candles.append(
            Candle(
                open_time=armed_at + timedelta(hours=1),
                close_time=pullback_time,
                open=100.0,
                high=101.0,
                low=99.8,
                close=100.0,
                volume=1000.0,
            )
        )
        candles.append(
            Candle(
                open_time=pullback_time,
                close_time=pullback_time + timedelta(hours=1),
                open=100.0,
                high=100.0,
                low=98.5,
                close=99.0,
                volume=1000.0,
            )
        )

        out = await service.resolve_pending_retest(
            session,
            _StubCandleProvider(candles),
            end_time=armed_at + timedelta(hours=6),
        )
        assert out.filled == 1
        assert position.status == "open"
        assert ensure_utc(position.opened_at) == pullback_time
        assert ensure_utc(position.expires_at) == pullback_time + timedelta(hours=24)

    @pytest.mark.asyncio
    async def test_fill_uses_worst_reachable_price_in_zone(
        self, session: AsyncSession
    ) -> None:
        candles = _flat_candles(datetime(2024, 6, 1, tzinfo=UTC), 21)
        arm_time = candles[-1].open_time
        candles.append(
            Candle(
                open_time=arm_time + timedelta(hours=1),
                close_time=arm_time + timedelta(hours=2),
                open=100.0,
                high=100.0,
                low=98.5,
                close=99.0,
                volume=1000.0,
            )
        )
        arm = arm_retest_entry(
            direction=SignalDirection.STRONG_LONG,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=95.0,
            timeframe="1h",
            candles=candles,
        )
        assert arm.filled
        assert arm.zone_hi is not None and arm.fill_price is not None
        # Kein Mittelpunkt: die Kerze ist nur bis zur Zonenoberkante handelbar.
        assert arm.fill_price == pytest.approx(arm.zone_hi)


class TestRiskNormalizedSizing:
    def _service(self, **overrides: object) -> PaperTradingService:
        base = {
            "enable_paper_trading": True,
            "paper_initial_balance": 5000.0,
            "paper_margin_per_trade": 100.0,
            "paper_leverage": 10.0,
            "paper_risk_per_trade_usd": 50.0,
            "paper_max_notional_usd": 1500.0,
            "paper_fee_percent": 0.0,
        }
        base.update(overrides)
        return PaperTradingService(Settings(**base))  # type: ignore[arg-type]

    def test_dollar_risk_is_constant_across_stop_distances(self) -> None:
        service = self._service()
        tight = service._size_position(Decimal("100"), Decimal("96"))
        wide = service._size_position(Decimal("100"), Decimal("88"))
        assert tight is not None and wide is not None
        assert float(tight.risk_amount) == pytest.approx(50.0)
        assert float(wide.risk_amount) == pytest.approx(50.0)
        assert wide.quantity < tight.quantity

    def test_notional_cap_limits_very_tight_stops(self) -> None:
        service = self._service()
        sizing = service._size_position(Decimal("100"), Decimal("99.9"))
        assert sizing is not None
        assert float(sizing.notional) == pytest.approx(1500.0)
        # Unter dem Cap traegt der Trade weniger als das volle Risiko.
        assert float(sizing.risk_amount) == pytest.approx(1.5)
        assert float(sizing.margin) == pytest.approx(150.0)

    def test_zero_risk_budget_falls_back_to_fixed_margin(self) -> None:
        service = self._service(paper_risk_per_trade_usd=0.0)
        sizing = service._size_position(Decimal("100"), Decimal("95"))
        assert sizing is not None
        assert float(sizing.margin) == pytest.approx(100.0)
        assert float(sizing.notional) == pytest.approx(1000.0)


class TestPaperTrading:
    @pytest.mark.asyncio
    async def test_open_and_scale_out_to_breakeven(self, session: AsyncSession) -> None:
        settings = Settings(
            enable_paper_trading=True,
            paper_initial_balance=2000.0,
            paper_margin_per_trade=100.0,
            paper_leverage=5.0,
            paper_risk_per_trade_usd=50.0,
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
        # Stop 5 Punkte entfernt, 50 USD Risiko -> 10 Stueck, 1.000 USD Nominal.
        assert float(position.notional) == pytest.approx(1000.0)
        assert float(position.remaining_quantity) == pytest.approx(10.0)
        assert float(position.risk_amount) == pytest.approx(50.0)

        account = await service.get_or_create_account(session)
        assert float(account.cash_balance) == pytest.approx(1800.0)

        updated = await service.update_open_positions(session, {"BTCUSDT": 105.0})
        assert len(updated) == 1
        assert position.tp1_filled is True
        assert float(position.current_stop) == pytest.approx(100.0)
        assert float(position.remaining_quantity) < 10.0

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
    async def test_notifies_on_open_only(self, session: AsyncSession) -> None:
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

        assert events == [("open", "BTCUSDT")]
