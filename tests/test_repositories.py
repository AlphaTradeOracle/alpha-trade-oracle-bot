"""Integrationstests der Repository-Schicht.

Die Tests laufen gegen eine echte, aber fluechtige SQLite-Datenbank. Damit
werden Schema, Constraints und Abfragen wirklich ausgefuehrt, ohne dass ein
PostgreSQL-Server noetig ist. Produktiv laeuft die Anwendung auf PostgreSQL;
dialektabhaengiges SQL liegt deshalb gebuendelt in ``app.database.dialects``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DeliveryStatus, SignalDirection, SuppressionReason
from app.market_data.types import Candle, CandleSeries, SymbolInfo
from app.repositories.asset_repository import AssetRepository
from app.repositories.chat_repository import ChatRepository, WatchlistRepository
from app.repositories.event_repository import EventRepository, ScheduledJobRepository
from app.repositories.signal_repository import SignalRepository
from app.repositories.strategy_repository import StrategyRepository
from app.strategies.weights import StrategyWeights
from tests.test_dedup import make_result

BTC = SymbolInfo(
    symbol="BTCUSDT",
    base_asset="BTC",
    quote_asset="USDT",
    price_precision=2,
    quantity_precision=6,
    is_active=True,
)
ETH = SymbolInfo(
    symbol="ETHUSDT",
    base_asset="ETH",
    quote_asset="USDT",
    price_precision=2,
    quantity_precision=5,
    is_active=True,
)


def make_series(count: int = 3, *, start: datetime | None = None) -> CandleSeries:
    begin = start or datetime(2024, 1, 1, tzinfo=UTC)
    candles = [
        Candle(
            open_time=begin + timedelta(hours=index),
            close_time=begin + timedelta(hours=index + 1),
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.5 + index,
            volume=1_000.0 + index,
            quote_volume=100_500.0,
            trade_count=42,
            is_closed=True,
        )
        for index in range(count)
    ]
    return CandleSeries(symbol="BTCUSDT", timeframe="1h", candles=candles)


class TestAssetRepository:
    @pytest.mark.asyncio
    async def test_creates_asset_from_symbol_info(self, session: AsyncSession) -> None:
        asset = await AssetRepository(session).get_or_create(BTC)

        assert asset.id is not None
        assert asset.symbol == "BTCUSDT"
        assert asset.base_asset == "BTC"
        assert asset.price_precision == 2

    @pytest.mark.asyncio
    async def test_get_or_create_is_idempotent(self, session: AsyncSession) -> None:
        repository = AssetRepository(session)
        first = await repository.get_or_create(BTC)
        second = await repository.get_or_create(BTC)

        assert first.id == second.id
        assert len(await repository.list_active()) == 1

    @pytest.mark.asyncio
    async def test_precision_change_is_adopted(self, session: AsyncSession) -> None:
        """Boersen aendern die Tickgroesse gelegentlich."""
        repository = AssetRepository(session)
        await repository.get_or_create(BTC)

        updated = await repository.get_or_create(
            SymbolInfo(
                symbol="BTCUSDT",
                base_asset="BTC",
                quote_asset="USDT",
                price_precision=4,
                quantity_precision=6,
                is_active=True,
            )
        )
        assert updated.price_precision == 4

    @pytest.mark.asyncio
    async def test_symbol_lookup_is_case_insensitive(self, session: AsyncSession) -> None:
        repository = AssetRepository(session)
        await repository.get_or_create(BTC)
        assert await repository.get_by_symbol("btcusdt") is not None

    @pytest.mark.asyncio
    async def test_resolves_many_symbols_in_one_query(self, session: AsyncSession) -> None:
        repository = AssetRepository(session)
        btc = await repository.get_or_create(BTC)
        eth = await repository.get_or_create(ETH)

        mapping = await repository.get_symbols_by_ids([btc.id, eth.id])
        assert mapping == {btc.id: "BTCUSDT", eth.id: "ETHUSDT"}

    @pytest.mark.asyncio
    async def test_unknown_ids_are_simply_absent(self, session: AsyncSession) -> None:
        assert await AssetRepository(session).get_symbols_by_ids([999]) == {}
        assert await AssetRepository(session).get_symbols_by_ids([]) == {}

    @pytest.mark.asyncio
    async def test_writes_candles(self, session: AsyncSession) -> None:
        repository = AssetRepository(session)
        asset = await repository.get_or_create(BTC)

        written = await repository.upsert_candles(asset.id, make_series(3))
        assert written == 3

    @pytest.mark.asyncio
    async def test_repeated_import_does_not_duplicate_candles(self, session: AsyncSession) -> None:
        """Ueberlappende Scans duerfen keine Doubletten erzeugen."""
        repository = AssetRepository(session)
        asset = await repository.get_or_create(BTC)
        series = make_series(3)

        await repository.upsert_candles(asset.id, series)
        await repository.upsert_candles(asset.id, series)

        from sqlalchemy import func, select

        from app.models.market import MarketCandle

        count = await session.execute(select(func.count(MarketCandle.id)))
        assert count.scalar_one() == 3

    @pytest.mark.asyncio
    async def test_empty_series_writes_nothing(self, session: AsyncSession) -> None:
        repository = AssetRepository(session)
        asset = await repository.get_or_create(BTC)
        empty = CandleSeries(symbol="BTCUSDT", timeframe="1h", candles=[])

        assert await repository.upsert_candles(asset.id, empty) == 0


class TestSignalRepository:
    @pytest.mark.asyncio
    async def test_persists_signal_with_all_core_fields(self, session: AsyncSession) -> None:
        asset = await AssetRepository(session).get_or_create(BTC)
        result = make_result()

        signal = await SignalRepository(session).create(result, asset.id)

        assert signal.id is not None
        assert signal.direction == result.direction.value
        assert float(signal.score) == pytest.approx(result.score)
        assert signal.fingerprint == result.fingerprint
        assert signal.primary_timeframe == result.primary_timeframe

    @pytest.mark.asyncio
    async def test_signal_levels_are_stored_as_columns_not_json(
        self, session: AsyncSession
    ) -> None:
        """Entry, Stop und Ziele muessen auswertbar sein, nicht nur lesbar."""
        asset = await AssetRepository(session).get_or_create(BTC)
        result = make_result()
        assert result.risk is not None

        signal = await SignalRepository(session).create(result, asset.id)

        assert float(signal.stop_loss) == pytest.approx(result.risk.stop_loss)
        assert float(signal.take_profit_1) == pytest.approx(result.risk.take_profit_1)
        assert float(signal.take_profit_2) == pytest.approx(result.risk.take_profit_2)
        assert float(signal.entry_low) == pytest.approx(result.risk.entry_low)

    @pytest.mark.asyncio
    async def test_score_breakdown_is_persisted(self, session: AsyncSession) -> None:
        asset = await AssetRepository(session).get_or_create(BTC)
        result = make_result()

        signal = await SignalRepository(session).create(result, asset.id)

        assert len(signal.score_components) == len(result.components)
        stored = {component.category for component in signal.score_components}
        expected = {component.category.value for component in result.components}
        assert stored == expected

    @pytest.mark.asyncio
    async def test_reads_signal_back_by_id(self, session: AsyncSession) -> None:
        asset = await AssetRepository(session).get_or_create(BTC)
        repository = SignalRepository(session)
        created = await repository.create(make_result(), asset.id)

        assert (await repository.get_by_id(created.id)).id == created.id
        assert await repository.get_by_id(999_999) is None

    @pytest.mark.asyncio
    async def test_lists_signals_filtered_by_direction(self, session: AsyncSession) -> None:
        asset = await AssetRepository(session).get_or_create(BTC)
        repository = SignalRepository(session)
        await repository.create(make_result(direction=SignalDirection.LONG), asset.id)
        await repository.create(make_result(direction=SignalDirection.SHORT), asset.id)
        await session.commit()

        longs = await repository.list_recent(direction=SignalDirection.LONG)
        assert len(longs) == 1
        assert longs[0].direction == "LONG"

    @pytest.mark.asyncio
    async def test_lists_signals_filtered_by_symbol(self, session: AsyncSession) -> None:
        assets = AssetRepository(session)
        btc = await assets.get_or_create(BTC)
        eth = await assets.get_or_create(ETH)
        repository = SignalRepository(session)
        await repository.create(make_result(), btc.id)
        await repository.create(make_result(), eth.id)
        await session.commit()

        assert len(await repository.list_recent(symbol="ETHUSDT")) == 1

    @pytest.mark.asyncio
    async def test_pagination_limits_results(self, session: AsyncSession) -> None:
        asset = await AssetRepository(session).get_or_create(BTC)
        repository = SignalRepository(session)
        for _ in range(5):
            await repository.create(make_result(), asset.id)
        await session.commit()

        assert len(await repository.list_recent(limit=2)) == 2
        assert len(await repository.list_recent(limit=2, offset=4)) == 1

    @pytest.mark.asyncio
    async def test_records_successful_delivery(self, session: AsyncSession) -> None:
        asset = await AssetRepository(session).get_or_create(BTC)
        repository = SignalRepository(session)
        signal = await repository.create(make_result(), asset.id)

        delivery = await repository.record_delivery(
            signal.id, 12345, status=DeliveryStatus.SENT, message_id=99
        )

        assert delivery.status == DeliveryStatus.SENT.value
        assert delivery.sent_at is not None
        assert delivery.message_id == 99

    @pytest.mark.asyncio
    async def test_records_suppression_with_reason(self, session: AsyncSession) -> None:
        """Auch unterdrueckte Signale werden protokolliert — sonst fehlt die Spur."""
        asset = await AssetRepository(session).get_or_create(BTC)
        repository = SignalRepository(session)
        signal = await repository.create(make_result(), asset.id)

        delivery = await repository.record_delivery(
            signal.id,
            12345,
            status=DeliveryStatus.SUPPRESSED,
            suppression_reason=SuppressionReason.COOLDOWN,
        )

        assert delivery.suppression_reason == SuppressionReason.COOLDOWN.value
        assert delivery.sent_at is None

    @pytest.mark.asyncio
    async def test_marks_signal_as_dispatched(self, session: AsyncSession) -> None:
        asset = await AssetRepository(session).get_or_create(BTC)
        repository = SignalRepository(session)
        signal = await repository.create(make_result(), asset.id)
        assert signal.is_dispatched is False

        await repository.mark_dispatched(signal.id)
        assert signal.is_dispatched is True

    @pytest.mark.asyncio
    async def test_finds_last_dispatched_signal_for_dedup(self, session: AsyncSession) -> None:
        asset = await AssetRepository(session).get_or_create(BTC)
        repository = SignalRepository(session)
        signal = await repository.create(make_result(), asset.id)
        await repository.mark_dispatched(signal.id)
        await session.commit()

        previous = await repository.get_last_dispatched("BTCUSDT", signal.primary_timeframe)
        assert previous is not None
        assert previous.fingerprint == signal.fingerprint
        assert previous.direction == SignalDirection(signal.direction)

    @pytest.mark.asyncio
    async def test_undispatched_signals_are_ignored_by_dedup_lookup(
        self, session: AsyncSession
    ) -> None:
        asset = await AssetRepository(session).get_or_create(BTC)
        repository = SignalRepository(session)
        signal = await repository.create(make_result(), asset.id)
        await session.commit()

        assert await repository.get_last_dispatched("BTCUSDT", signal.primary_timeframe) is None

    @pytest.mark.asyncio
    async def test_performance_summary_on_empty_database(self, session: AsyncSession) -> None:
        summary = await SignalRepository(session).performance_summary(days=30)

        assert summary["signals_total"] == 0
        assert summary["average_score"] == 0.0
        assert summary["signals_dispatched"] == 0

    @pytest.mark.asyncio
    async def test_performance_summary_aggregates_signals(self, session: AsyncSession) -> None:
        asset = await AssetRepository(session).get_or_create(BTC)
        repository = SignalRepository(session)
        first = await repository.create(make_result(score=60.0), asset.id)
        await repository.create(make_result(score=80.0), asset.id)
        await repository.mark_dispatched(first.id)
        await session.commit()

        summary = await repository.performance_summary(days=30)

        assert summary["signals_total"] == 2
        assert summary["signals_dispatched"] == 1
        assert summary["average_score"] == pytest.approx(70.0)
        assert summary["count_long"] == 2


class TestChatAndWatchlistRepository:
    @pytest.mark.asyncio
    async def test_creates_chat(self, session: AsyncSession) -> None:
        chat = await ChatRepository(session).get_or_create(555, title="Testchat")

        assert chat.id is not None
        assert chat.chat_id == 555
        assert chat.is_active is True

    @pytest.mark.asyncio
    async def test_get_or_create_returns_existing_chat(self, session: AsyncSession) -> None:
        repository = ChatRepository(session)
        first = await repository.get_or_create(555)
        second = await repository.get_or_create(555)
        assert first.id == second.id

    @pytest.mark.asyncio
    async def test_admin_flag_follows_configuration(self, session: AsyncSession) -> None:
        """Wird eine Chat-ID aus der Adminliste entfernt, muss das sofort wirken."""
        repository = ChatRepository(session)
        await repository.get_or_create(555, is_admin=True)
        downgraded = await repository.get_or_create(555, is_admin=False)
        assert downgraded.is_admin is False

    @pytest.mark.asyncio
    async def test_notifications_can_be_disabled(self, session: AsyncSession) -> None:
        repository = ChatRepository(session)
        await repository.get_or_create(555)
        await repository.set_notifications(555, False)
        await session.commit()

        assert await repository.list_active_with_notifications() == []

    @pytest.mark.asyncio
    async def test_adds_symbol_to_watchlist(self, session: AsyncSession) -> None:
        chat = await ChatRepository(session).get_or_create(555)
        asset = await AssetRepository(session).get_or_create(BTC)

        entry, created = await WatchlistRepository(session).add(chat.id, asset.id)

        assert created is True
        assert entry.is_active is True

    @pytest.mark.asyncio
    async def test_adding_twice_reports_no_new_entry(self, session: AsyncSession) -> None:
        chat = await ChatRepository(session).get_or_create(555)
        asset = await AssetRepository(session).get_or_create(BTC)
        repository = WatchlistRepository(session)

        await repository.add(chat.id, asset.id)
        _, created_again = await repository.add(chat.id, asset.id)
        assert created_again is False

    @pytest.mark.asyncio
    async def test_removal_is_soft_and_reactivation_works(self, session: AsyncSession) -> None:
        """Der Eintrag bleibt fuer Auswertungen erhalten und kann zurueckkehren."""
        chat = await ChatRepository(session).get_or_create(555)
        asset = await AssetRepository(session).get_or_create(BTC)
        repository = WatchlistRepository(session)
        await repository.add(chat.id, asset.id)

        assert await repository.remove(chat.id, asset.id) is True
        assert await repository.list_for_chat(chat.id) == []

        _, reactivated = await repository.add(chat.id, asset.id)
        assert reactivated is True
        assert len(await repository.list_for_chat(chat.id)) == 1

    @pytest.mark.asyncio
    async def test_removing_unknown_entry_reports_false(self, session: AsyncSession) -> None:
        chat = await ChatRepository(session).get_or_create(555)
        asset = await AssetRepository(session).get_or_create(BTC)
        assert await WatchlistRepository(session).remove(chat.id, asset.id) is False

    @pytest.mark.asyncio
    async def test_scan_symbols_are_deduplicated_across_chats(self, session: AsyncSession) -> None:
        chats = ChatRepository(session)
        first = await chats.get_or_create(1)
        second = await chats.get_or_create(2)
        assets = AssetRepository(session)
        btc = await assets.get_or_create(BTC)
        eth = await assets.get_or_create(ETH)

        watchlists = WatchlistRepository(session)
        await watchlists.add(first.id, btc.id)
        await watchlists.add(second.id, btc.id)
        await watchlists.add(second.id, eth.id)
        await session.commit()

        assert await watchlists.distinct_watched_symbols() == ["BTCUSDT", "ETHUSDT"]

    @pytest.mark.asyncio
    async def test_muted_chats_are_excluded_from_scans(self, session: AsyncSession) -> None:
        chats = ChatRepository(session)
        chat = await chats.get_or_create(1)
        asset = await AssetRepository(session).get_or_create(BTC)
        await WatchlistRepository(session).add(chat.id, asset.id)
        await chats.set_notifications(1, False)
        await session.commit()

        assert await WatchlistRepository(session).distinct_watched_symbols() == []


#: Gueltige Alternativgewichtung: die Summe muss weiterhin 1.0 ergeben.
CANDIDATE_WEIGHTS = StrategyWeights(
    trend=0.30,
    momentum=0.18,
    volume=0.14,
    market_structure=0.14,
    multi_timeframe=0.14,
    volatility=0.04,
    sentiment=0.03,
    risk_reward=0.03,
)


class TestStrategyRepository:
    @pytest.mark.asyncio
    async def test_creates_strategy_and_first_version(self, session: AsyncSession) -> None:
        repository = StrategyRepository(session)
        version = await repository.create_version(StrategyWeights(), activate=True)

        assert version.id is not None
        assert version.version == 1
        assert version.is_active is True

    @pytest.mark.asyncio
    async def test_new_version_is_inactive_by_default(self, session: AsyncSession) -> None:
        """Eine neue Gewichtung darf nicht ungeprueft produktiv werden."""
        repository = StrategyRepository(session)
        candidate = await repository.create_version(StrategyWeights())
        assert candidate.is_active is False

    @pytest.mark.asyncio
    async def test_versions_increment_instead_of_overwriting(self, session: AsyncSession) -> None:
        repository = StrategyRepository(session)
        base = await repository.create_version(StrategyWeights(), activate=True)
        candidate = await repository.create_version(
            CANDIDATE_WEIGHTS, notes="Kandidat aus Kalibrierung"
        )

        assert candidate.id != base.id
        assert candidate.version == base.version + 1
        assert len(await repository.list_versions()) == 2

    @pytest.mark.asyncio
    async def test_activation_deactivates_the_previous_version(self, session: AsyncSession) -> None:
        repository = StrategyRepository(session)
        base = await repository.create_version(StrategyWeights(), activate=True)
        candidate = await repository.create_version(CANDIDATE_WEIGHTS)

        await repository.activate_version(candidate.id)

        assert base.is_active is False
        assert candidate.is_active is True
        assert candidate.activated_at is not None

    @pytest.mark.asyncio
    async def test_weights_survive_a_round_trip(self, session: AsyncSession) -> None:
        repository = StrategyRepository(session)
        await repository.create_version(CANDIDATE_WEIGHTS, activate=True)
        await session.commit()

        loaded, version_id = await repository.load_weights()

        assert version_id is not None
        assert loaded == CANDIDATE_WEIGHTS

    @pytest.mark.asyncio
    async def test_missing_strategy_yields_no_weights(self, session: AsyncSession) -> None:
        assert await StrategyRepository(session).load_weights() == (None, None)


class TestEventRepository:
    @pytest.mark.asyncio
    async def test_records_event_with_payload(self, session: AsyncSession) -> None:
        from app.core.enums import EventSeverity

        event = await EventRepository(session).record(
            "scan_completed",
            "Scan ueber 2 Symbole abgeschlossen",
            severity=EventSeverity.INFO,
            payload={"symbols": 2},
        )

        assert event.id is not None
        assert event.event_type == "scan_completed"
        assert event.payload == {"symbols": 2}

    @pytest.mark.asyncio
    async def test_lists_events_filtered_by_type(self, session: AsyncSession) -> None:
        repository = EventRepository(session)
        await repository.record("scan_completed", "ok")
        await repository.record("scan_failed", "Marktdaten nicht erreichbar")
        await session.commit()

        assert len(await repository.list_recent(event_type="scan_failed")) == 1
        assert len(await repository.list_recent()) == 2


class TestScheduledJobRepository:
    @pytest.mark.asyncio
    async def test_registers_job_once(self, session: AsyncSession) -> None:
        repository = ScheduledJobRepository(session)
        first = await repository.register("scan:1h", "scan", 3600)
        second = await repository.register("scan:1h", "scan", 3600)
        assert first.id == second.id

    @pytest.mark.asyncio
    async def test_second_claim_within_interval_is_refused(self, session: AsyncSession) -> None:
        """Verhindert, dass zwei Worker denselben Scan doppelt ausfuehren."""
        repository = ScheduledJobRepository(session)
        await repository.register("scan:1h", "scan", 3600)

        assert await repository.claim("scan:1h") is True
        assert await repository.claim("scan:1h") is False

    @pytest.mark.asyncio
    async def test_unknown_job_cannot_be_claimed(self, session: AsyncSession) -> None:
        assert await ScheduledJobRepository(session).claim("gibt-es-nicht") is False

    @pytest.mark.asyncio
    async def test_completion_records_failure_reason(self, session: AsyncSession) -> None:
        repository = ScheduledJobRepository(session)
        await repository.register("scan:1h", "scan", 3600)
        await repository.claim("scan:1h")

        await repository.complete("scan:1h", success=False, error="Timeout")

        job = await repository.get("scan:1h")
        assert job.last_status == "failed"
        assert job.last_error == "Timeout"
        assert job.last_success_at is None

    @pytest.mark.asyncio
    async def test_disable_job_types(self, session: AsyncSession) -> None:
        repository = ScheduledJobRepository(session)
        await repository.register("paper_digest:60m", "paper_digest", 3600)
        await repository.register("market_scan:15m", "market_scan", 900)

        disabled = await repository.disable_job_types(
            {"paper_digest"}, reason="disabled by test"
        )
        assert disabled == ["paper_digest:60m"]

        digest = await repository.get("paper_digest:60m")
        scan = await repository.get("market_scan:15m")
        assert digest is not None and digest.is_enabled is False
        assert digest.last_status == "disabled"
        assert scan is not None and scan.is_enabled is True
