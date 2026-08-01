"""Tests fuer AnalysisService und ScanService.

Beide nutzen einen Stub-Marktdaten-Provider mit synthetischen Kerzen. Damit
pruefen die Tests den vertikalen Ablauf, ohne Binance oder Telegram zu brauchen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.enums import SignalDirection
from app.core.errors import InsufficientDataError, MarketDataError, SymbolNotFoundError
from app.market_data.types import Candle, CandleSeries, SymbolInfo
from app.services.analysis_service import AnalysisService
from app.services.scan_service import ScanService, SignalDispatcher
from app.signals.dedup import DedupDecision, SignalDeduplicator
from app.signals.types import SignalResult

BTC = SymbolInfo(
    symbol="BTCUSDT",
    base_asset="BTC",
    quote_asset="USDT",
    price_precision=2,
    quantity_precision=6,
    is_active=True,
)


def dataframe_to_series(
    frame: pd.DataFrame, *, symbol: str = "BTCUSDT", timeframe: str = "1h"
) -> CandleSeries:
    """OHLCV-DataFrame in eine CandleSeries umwandeln."""
    minutes = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}[timeframe]
    candles: list[Candle] = []
    for open_time, row in frame.iterrows():
        open_dt = open_time.to_pydatetime()
        if open_dt.tzinfo is None:
            open_dt = open_dt.replace(tzinfo=UTC)
        candles.append(
            Candle(
                open_time=open_dt,
                close_time=open_dt + timedelta(minutes=minutes),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                is_closed=True,
            )
        )
    return CandleSeries(symbol=symbol, timeframe=timeframe, candles=candles)


class StubProvider:
    """Deterministischer Marktdaten-Provider fuer Service-Tests."""

    name = "stub"

    def __init__(
        self,
        frames: dict[str, pd.DataFrame] | None = None,
        *,
        info: SymbolInfo = BTC,
        fail_timeframes: set[str] | None = None,
    ) -> None:
        self._frames = frames or {}
        self._info = info
        self._fail_timeframes = fail_timeframes or set()
        self.calls: list[str] = []

    async def list_symbols(self, quote_asset: str | None = None) -> list[SymbolInfo]:
        return [self._info]

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        if symbol.upper() != self._info.symbol:
            raise SymbolNotFoundError(symbol)
        return self._info

    async def get_price(self, symbol: str) -> float:
        series = await self.get_candles(symbol, "1h", limit=1)
        return float(series.last_close or 0.0)

    async def get_prices(self, symbols: list[str]) -> dict[str, float]:
        return {symbol: await self.get_price(symbol) for symbol in symbols}

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 500,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> CandleSeries:
        self.calls.append(f"{symbol}:{timeframe}")
        if timeframe in self._fail_timeframes:
            raise MarketDataError(f"Stub-Fehler fuer {timeframe}")
        frame = self._frames.get(timeframe)
        if frame is None:
            return CandleSeries(symbol=symbol, timeframe=timeframe, candles=[])
        truncated = frame.tail(limit)
        return dataframe_to_series(truncated, symbol=symbol.upper(), timeframe=timeframe)

    async def get_multi_timeframe_candles(
        self,
        symbol: str,
        timeframes: list[str],
        *,
        limit: int = 500,
    ) -> dict[str, CandleSeries]:
        result: dict[str, CandleSeries] = {}
        for timeframe in timeframes:
            try:
                series = await self.get_candles(symbol, timeframe, limit=limit)
            except MarketDataError:
                continue
            if not series.is_empty:
                result[timeframe] = series
        return result

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class RecordingDispatcher(SignalDispatcher):
    def __init__(self) -> None:
        self.dispatched: list[SignalResult] = []

    async def dispatch(self, outcome):  # type: ignore[no-untyped-def]
        self.dispatched.append(outcome.result)
        return [(1, 42, None)]


class RecordingDedup(SignalDeduplicator):
    def __init__(self) -> None:
        super().__init__(cooldown_minutes=0)
        self.recorded: list[SignalResult] = []

    async def record_dispatch(self, result: SignalResult) -> None:
        self.recorded.append(result)
        await super().record_dispatch(result)


class AlwaysSuppressDedup(SignalDeduplicator):
    async def evaluate(self, result, **_kwargs):  # type: ignore[no-untyped-def]
        from app.core.enums import SuppressionReason

        return DedupDecision(
            should_send=False,
            reason=SuppressionReason.BELOW_MIN_SCORE,
            detail="unter Mindestscore",
        )


def service_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "enable_llm_analysis": False,
        "enable_sentiment": False,
        "enable_universe_scan": False,
        # Tests mock exchanges; skip live perp venue lookups by default.
        "universe_require_leverage": False,
        "default_timeframes": "15m,1h,4h,1d",
        "min_candles_required": 210,
        "candle_limit": 400,
        "signal_min_score": 65.0,
        "min_risk_reward_ratio": 2.0,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
def uptrend_frames(uptrend_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return dict.fromkeys(("15m", "1h", "4h", "1d"), uptrend_df)


class TestAnalysisService:
    @pytest.mark.asyncio
    async def test_produces_signal_without_persistence(
        self, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        service = AnalysisService(StubProvider(uptrend_frames), settings=service_settings())

        outcome = await service.analyze("BTCUSDT", persist=False)

        assert outcome.signal_id is None
        assert outcome.result.symbol == "BTCUSDT"
        assert outcome.result.direction in SignalDirection
        assert 0.0 <= outcome.result.score <= 100.0
        assert outcome.result.analyzed_timeframes
        assert outcome.price_precision == 2

    @pytest.mark.asyncio
    async def test_uptrend_tends_towards_long(
        self, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        service = AnalysisService(StubProvider(uptrend_frames), settings=service_settings())

        outcome = await service.analyze("btcusdt", persist=False)

        assert outcome.result.direction in (
            SignalDirection.LONG,
            SignalDirection.STRONG_LONG,
            SignalDirection.NEUTRAL,
            SignalDirection.NO_TRADE,
        )
        assert outcome.result.score >= 40.0

    @pytest.mark.asyncio
    async def test_persists_signal_when_session_is_provided(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        service = AnalysisService(StubProvider(uptrend_frames), settings=service_settings())

        outcome = await service.analyze("BTCUSDT", session=session, persist=True)

        assert outcome.signal_id is not None
        assert outcome.asset_id is not None

    @pytest.mark.asyncio
    async def test_skips_missing_timeframes_without_aborting(
        self, uptrend_df: pd.DataFrame
    ) -> None:
        frames = {"1h": uptrend_df, "4h": uptrend_df}
        service = AnalysisService(StubProvider(frames), settings=service_settings())

        outcome = await service.analyze(
            "BTCUSDT",
            timeframes=["15m", "1h", "4h", "1d"],
            persist=False,
        )

        assert "15m" in outcome.skipped_timeframes
        assert "1d" in outcome.skipped_timeframes
        assert "1h" in outcome.result.analyzed_timeframes

    @pytest.mark.asyncio
    async def test_unknown_symbol_raises(self, uptrend_frames: dict[str, pd.DataFrame]) -> None:
        service = AnalysisService(StubProvider(uptrend_frames), settings=service_settings())

        with pytest.raises(SymbolNotFoundError):
            await service.analyze("NOPEUSDT", persist=False)

    @pytest.mark.asyncio
    async def test_inactive_symbol_raises(self, uptrend_frames: dict[str, pd.DataFrame]) -> None:
        inactive = SymbolInfo(
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            is_active=False,
        )
        service = AnalysisService(
            StubProvider(uptrend_frames, info=inactive), settings=service_settings()
        )

        with pytest.raises(MarketDataError):
            await service.analyze("BTCUSDT", persist=False)

    @pytest.mark.asyncio
    async def test_no_usable_timeframes_raises(self) -> None:
        service = AnalysisService(StubProvider({}), settings=service_settings())

        with pytest.raises((MarketDataError, InsufficientDataError)):
            await service.analyze("BTCUSDT", persist=False)

    @pytest.mark.asyncio
    async def test_works_without_llm(self, uptrend_frames: dict[str, pd.DataFrame]) -> None:
        service = AnalysisService(StubProvider(uptrend_frames), settings=service_settings())

        outcome = await service.analyze("BTCUSDT", persist=False, use_llm=True)

        assert outcome.llm_analysis is None
        assert outcome.result.direction in SignalDirection

    @pytest.mark.asyncio
    async def test_risk_levels_present_for_actionable_signals(
        self, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        service = AnalysisService(StubProvider(uptrend_frames), settings=service_settings())
        outcome = await service.analyze("BTCUSDT", persist=False)

        if outcome.result.direction.is_actionable:
            assert outcome.result.risk is not None
            assert outcome.result.risk.stop_loss > 0
            assert outcome.result.risk.take_profit_1 > 0
            assert outcome.result.risk.take_profit_2 > 0


class TestScanService:
    @pytest.mark.asyncio
    async def test_scan_uses_explicit_symbols(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        analysis = AnalysisService(StubProvider(uptrend_frames), settings=service_settings())
        scan = ScanService(
            analysis, SignalDeduplicator(cooldown_minutes=0), settings=service_settings()
        )

        result = await scan.scan(session, symbols=["BTCUSDT"], dispatch=False)

        assert result.symbols_scanned == 1
        assert result.signals_created == 1
        assert result.failures == []

    @pytest.mark.asyncio
    async def test_scan_falls_back_to_default_symbols(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        settings = service_settings(default_symbols="BTCUSDT")
        analysis = AnalysisService(StubProvider(uptrend_frames), settings=settings)
        scan = ScanService(analysis, SignalDeduplicator(cooldown_minutes=0), settings=settings)

        result = await scan.scan(session, dispatch=False)

        assert result.symbols_scanned == 1
        assert result.signals_created == 1

    @pytest.mark.asyncio
    async def test_scan_continues_after_symbol_failure(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        analysis = AnalysisService(StubProvider(uptrend_frames), settings=service_settings())
        scan = ScanService(
            analysis, SignalDeduplicator(cooldown_minutes=0), settings=service_settings()
        )

        result = await scan.scan(session, symbols=["NOPEUSDT", "BTCUSDT"], dispatch=False)

        assert result.symbols_scanned == 2
        assert result.signals_created == 1
        assert len(result.failures) == 1
        assert result.failures[0][0] == "NOPEUSDT"

    @pytest.mark.asyncio
    async def test_suppressed_signals_are_counted(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        analysis = AnalysisService(StubProvider(uptrend_frames), settings=service_settings())
        scan = ScanService(analysis, AlwaysSuppressDedup(), settings=service_settings())

        result = await scan.scan(session, symbols=["BTCUSDT"], dispatch=False)

        assert result.signals_suppressed == 1
        assert result.signals_dispatched == 0

    @pytest.mark.asyncio
    async def test_dispatcher_is_invoked_when_allowed(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        analysis = AnalysisService(StubProvider(uptrend_frames), settings=service_settings())
        dispatcher = RecordingDispatcher()
        settings = service_settings(signal_min_score=0.0, min_risk_reward_ratio=0.01)
        scan = ScanService(
            analysis,
            SignalDeduplicator(cooldown_minutes=0),
            dispatcher=dispatcher,
            settings=settings,
        )

        result = await scan.scan(session, symbols=["BTCUSDT"], dispatch=True)

        assert result.symbols_scanned == 1
        if result.signals_dispatched:
            assert len(dispatcher.dispatched) == 1

    @pytest.mark.asyncio
    async def test_paper_only_scan_records_dedup_without_dispatcher(
        self, session: AsyncSession, uptrend_frames: dict[str, pd.DataFrame]
    ) -> None:
        analysis = AnalysisService(StubProvider(uptrend_frames), settings=service_settings())
        dedup = RecordingDedup()
        settings = service_settings(signal_min_score=0.0, min_risk_reward_ratio=0.01)
        scan = ScanService(
            analysis,
            dedup,
            dispatcher=None,
            settings=settings,
        )

        result = await scan.scan(session, symbols=["BTCUSDT"], dispatch=True)

        assert result.symbols_scanned == 1
        assert result.signals_dispatched == 0
        if result.signals_created and not result.signals_suppressed:
            assert len(dedup.recorded) == 1

    @pytest.mark.asyncio
    async def test_empty_target_list_is_a_noop(self, session: AsyncSession) -> None:
        analysis = AnalysisService(StubProvider({}), settings=service_settings())
        scan = ScanService(
            analysis,
            SignalDeduplicator(),
            settings=service_settings(default_symbols=""),
        )

        result = await scan.scan(session, symbols=[], dispatch=False)

        assert result.symbols_scanned == 0
        assert result.signals_created == 0
