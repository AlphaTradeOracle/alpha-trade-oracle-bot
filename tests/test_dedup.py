"""Tests der Signal-Deduplizierung und der Versandbedingungen."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import Confidence, MarketPhase, SignalDirection, SuppressionReason
from app.signals.dedup import PreviousSignal, SignalDeduplicator
from app.signals.types import RiskParameters, SignalResult

NOW = datetime(2024, 6, 1, 12, tzinfo=UTC)


def make_risk(*, entry_mid: float = 40_000.0, ratio: float = 2.5) -> RiskParameters:
    return RiskParameters(
        entry_low=entry_mid - 100.0,
        entry_high=entry_mid + 100.0,
        stop_loss=entry_mid - 600.0,
        take_profit_1=entry_mid + 900.0,
        take_profit_2=entry_mid + 1_500.0,
        take_profit_3=entry_mid + 2_400.0,
        risk_reward_ratio=ratio,
        risk_percent=1.0,
        suggested_position_size=0.16,
        stop_distance_percent=1.5,
        invalidation_note="4h-Schlusskurs unter 39.400",
    )


def make_result(
    *,
    direction: SignalDirection = SignalDirection.LONG,
    score: float = 72.0,
    data_quality: float = 95.0,
    entry_mid: float = 40_000.0,
    ratio: float = 2.5,
    fingerprint: str = "fp-a",
    created_at: datetime = NOW,
    expires_at: datetime | None = None,
) -> SignalResult:
    result = SignalResult(
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=expires_at or created_at + timedelta(hours=4),
        direction=direction,
        score=score,
        confidence=Confidence.MEDIUM,
        market_phase=MarketPhase.UPTREND,
        primary_timeframe="1h",
        analyzed_timeframes=["1h", "4h"],
        reference_price=entry_mid,
        data_quality=data_quality,
        # Der Deduplizierer bewertet nur das Ergebnis, nicht dessen Zustandekommen.
        components=[],
        assessments={},
        risk=make_risk(entry_mid=entry_mid, ratio=ratio) if direction.is_actionable else None,
    )
    result.fingerprint = fingerprint
    return result


class TestSendConditions:
    @pytest.mark.asyncio
    async def test_sends_first_signal(self) -> None:
        decision = await SignalDeduplicator().evaluate(
            make_result(), min_score=65.0, min_risk_reward_ratio=2.0, now=NOW
        )
        assert decision.should_send is True
        assert decision.reason is None

    @pytest.mark.asyncio
    async def test_suppresses_below_minimum_score(self) -> None:
        decision = await SignalDeduplicator().evaluate(
            make_result(score=50.0), min_score=65.0, min_risk_reward_ratio=2.0, now=NOW
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.BELOW_MIN_SCORE

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "direction",
        [SignalDirection.NEUTRAL, SignalDirection.NO_TRADE],
    )
    async def test_suppresses_non_actionable_direction(self, direction: SignalDirection) -> None:
        decision = await SignalDeduplicator().evaluate(
            make_result(direction=direction),
            min_score=0.0,
            min_risk_reward_ratio=0.0,
            now=NOW,
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.NOT_ACTIONABLE

    @pytest.mark.asyncio
    async def test_suppresses_expired_signal(self) -> None:
        decision = await SignalDeduplicator().evaluate(
            make_result(expires_at=NOW - timedelta(minutes=1)),
            min_score=0.0,
            min_risk_reward_ratio=0.0,
            now=NOW,
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.EXPIRED

    @pytest.mark.asyncio
    async def test_suppresses_low_data_quality(self) -> None:
        decision = await SignalDeduplicator().evaluate(
            make_result(data_quality=40.0),
            min_score=0.0,
            min_risk_reward_ratio=0.0,
            min_data_quality=60.0,
            now=NOW,
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.LOW_DATA_QUALITY

    @pytest.mark.asyncio
    async def test_suppresses_insufficient_risk_reward(self) -> None:
        decision = await SignalDeduplicator().evaluate(
            make_result(ratio=1.2), min_score=0.0, min_risk_reward_ratio=2.0, now=NOW
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.RISK_REWARD_TOO_LOW


class TestDuplicateDetection:
    @pytest.mark.asyncio
    async def test_identical_fingerprint_is_suppressed(self) -> None:
        dedup = SignalDeduplicator(cooldown_minutes=120)
        first = make_result(fingerprint="fp-same")
        await dedup.record_dispatch(first)

        second = make_result(fingerprint="fp-same", created_at=NOW + timedelta(minutes=5))
        decision = await dedup.evaluate(
            second, min_score=65.0, min_risk_reward_ratio=2.0, now=NOW + timedelta(minutes=5)
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.DUPLICATE

    @pytest.mark.asyncio
    async def test_identical_signal_suppressed_even_after_cooldown(self) -> None:
        """Der Fingerprint-Vergleich hat Vorrang vor dem Cooldown."""
        dedup = SignalDeduplicator(cooldown_minutes=60)
        await dedup.record_dispatch(make_result(fingerprint="fp-same"))

        later = NOW + timedelta(hours=10)
        decision = await dedup.evaluate(
            make_result(fingerprint="fp-same", created_at=later),
            min_score=65.0,
            min_risk_reward_ratio=2.0,
            now=later,
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.DUPLICATE


class TestCooldown:
    @pytest.mark.asyncio
    async def test_suppresses_within_cooldown_without_relevant_change(self) -> None:
        dedup = SignalDeduplicator(cooldown_minutes=120)
        await dedup.record_dispatch(make_result(score=72.0, fingerprint="fp-a"))

        later = NOW + timedelta(minutes=30)
        decision = await dedup.evaluate(
            make_result(score=74.0, fingerprint="fp-b", created_at=later),
            min_score=65.0,
            min_risk_reward_ratio=2.0,
            now=later,
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.COOLDOWN

    @pytest.mark.asyncio
    async def test_sends_after_cooldown_elapsed(self) -> None:
        dedup = SignalDeduplicator(cooldown_minutes=60)
        await dedup.record_dispatch(make_result(fingerprint="fp-a"))

        later = NOW + timedelta(minutes=90)
        decision = await dedup.evaluate(
            make_result(score=73.0, fingerprint="fp-b", created_at=later),
            min_score=65.0,
            min_risk_reward_ratio=2.0,
            now=later,
        )
        assert decision.should_send is True

    @pytest.mark.asyncio
    async def test_zero_cooldown_allows_immediate_resend(self) -> None:
        dedup = SignalDeduplicator(cooldown_minutes=0)
        await dedup.record_dispatch(make_result(fingerprint="fp-a"))
        decision = await dedup.evaluate(
            make_result(fingerprint="fp-b"), min_score=65.0, min_risk_reward_ratio=2.0, now=NOW
        )
        assert decision.should_send is True


class TestRelevantChange:
    @pytest.mark.asyncio
    async def test_direction_change_breaks_cooldown(self) -> None:
        dedup = SignalDeduplicator(cooldown_minutes=240)
        await dedup.record_dispatch(
            make_result(direction=SignalDirection.LONG, fingerprint="fp-long")
        )

        later = NOW + timedelta(minutes=10)
        decision = await dedup.evaluate(
            make_result(
                direction=SignalDirection.SHORT,
                score=30.0,
                fingerprint="fp-short",
                created_at=later,
            ),
            min_score=0.0,
            min_risk_reward_ratio=2.0,
            now=later,
        )
        assert decision.should_send is True
        assert "Richtungswechsel" in decision.detail

    @pytest.mark.asyncio
    async def test_large_score_change_breaks_cooldown(self) -> None:
        dedup = SignalDeduplicator(cooldown_minutes=240)
        await dedup.record_dispatch(make_result(score=68.0, fingerprint="fp-a"))

        later = NOW + timedelta(minutes=10)
        decision = await dedup.evaluate(
            make_result(score=88.0, fingerprint="fp-b", created_at=later),
            min_score=65.0,
            min_risk_reward_ratio=2.0,
            now=later,
        )
        assert decision.should_send is True
        assert "Score" in decision.detail

    @pytest.mark.asyncio
    async def test_small_score_change_does_not_break_cooldown(self) -> None:
        dedup = SignalDeduplicator(cooldown_minutes=240)
        await dedup.record_dispatch(make_result(score=70.0, fingerprint="fp-a"))

        later = NOW + timedelta(minutes=10)
        decision = await dedup.evaluate(
            make_result(score=73.0, fingerprint="fp-b", created_at=later),
            min_score=65.0,
            min_risk_reward_ratio=2.0,
            now=later,
        )
        assert decision.should_send is False


class TestHistoryFallback:
    @pytest.mark.asyncio
    async def test_uses_database_history_when_cache_is_empty(self) -> None:
        """Nach einem Redis-Neustart darf keine Signalflut entstehen."""

        class StubHistory:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            async def get_last_dispatched(
                self, symbol: str, timeframe: str
            ) -> PreviousSignal | None:
                self.calls.append((symbol, timeframe))
                return PreviousSignal(
                    fingerprint="fp-a",
                    direction=SignalDirection.LONG,
                    score=72.0,
                    entry_mid=40_000.0,
                    created_at=NOW,
                )

        history = StubHistory()
        dedup = SignalDeduplicator(cooldown_minutes=120, history_reader=history)

        decision = await dedup.evaluate(
            make_result(fingerprint="fp-a"),
            min_score=65.0,
            min_risk_reward_ratio=2.0,
            now=NOW + timedelta(minutes=5),
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.DUPLICATE
        assert history.calls == [("BTCUSDT", "1h")]

    @pytest.mark.asyncio
    async def test_failing_history_does_not_block_dispatch(self) -> None:
        class BrokenHistory:
            async def get_last_dispatched(
                self, symbol: str, timeframe: str
            ) -> PreviousSignal | None:
                raise RuntimeError("Datenbank nicht erreichbar")

        dedup = SignalDeduplicator(history_reader=BrokenHistory())
        decision = await dedup.evaluate(
            make_result(), min_score=65.0, min_risk_reward_ratio=2.0, now=NOW
        )
        assert decision.should_send is True


class TestRedisResilience:
    @pytest.mark.asyncio
    async def test_broken_redis_falls_back_to_memory(self) -> None:
        class BrokenRedis:
            async def hgetall(self, key: str) -> dict[str, str]:
                raise ConnectionError("Redis weg")

            async def hset(self, key: str, mapping: dict[str, str]) -> None:
                raise ConnectionError("Redis weg")

            async def expire(self, key: str, seconds: int) -> None:
                raise ConnectionError("Redis weg")

        dedup = SignalDeduplicator(cooldown_minutes=120, redis_client=BrokenRedis())
        await dedup.record_dispatch(make_result(fingerprint="fp-a"))

        decision = await dedup.evaluate(
            make_result(fingerprint="fp-a", created_at=NOW + timedelta(minutes=5)),
            min_score=65.0,
            min_risk_reward_ratio=2.0,
            now=NOW + timedelta(minutes=5),
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.DUPLICATE

    @pytest.mark.asyncio
    async def test_cooldown_is_isolated_per_symbol_and_timeframe(self) -> None:
        dedup = SignalDeduplicator(cooldown_minutes=240)
        await dedup.record_dispatch(make_result(fingerprint="fp-a"))

        other = make_result(fingerprint="fp-b")
        other.symbol = "ETHUSDT"
        decision = await dedup.evaluate(other, min_score=65.0, min_risk_reward_ratio=2.0, now=NOW)
        assert decision.should_send is True
