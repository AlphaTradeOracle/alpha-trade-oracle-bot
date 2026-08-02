"""MarketRegimeEngine — orchestrates all market-context analyzers."""

from __future__ import annotations

from typing import Mapping

import pandas as pd

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.time import utc_now
from app.indicators.engine import IndicatorEngine
from app.market_data.base import MarketDataProvider
from app.market_regime.bitcoin import DEFAULT_BTC_TIMEFRAMES, BitcoinAnalyzer
from app.market_regime.dominance import DominanceAnalyzer
from app.market_regime.ethereum import EthereumAnalyzer
from app.market_regime.fear_greed import FearGreedAnalyzer
from app.market_regime.funding import FundingAnalyzer
from app.market_regime.liquidations import LiquidationAnalyzer
from app.market_regime.liquidity import FreeVenueLiquidityFetcher
from app.market_regime.open_interest import OpenInterestAnalyzer
from app.market_regime.score import FinalScoreCalculator
from app.market_regime.sources import (
    DerivativesClient,
    DominanceClient,
    FearGreedClient,
)
from app.market_regime.types import (
    MarketBias,
    MarketRegimeSnapshot,
    ScoreWeights,
    bias_from_score,
    empty_snapshot,
)

logger = get_logger(__name__)


class MarketRegimeEngine:
    """Build a ``MarketRegimeSnapshot`` for the current market environment."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        indicator_engine: IndicatorEngine | None = None,
        bitcoin_analyzer: BitcoinAnalyzer | None = None,
        ethereum_analyzer: EthereumAnalyzer | None = None,
        dominance_analyzer: DominanceAnalyzer | None = None,
        fear_greed_analyzer: FearGreedAnalyzer | None = None,
        funding_analyzer: FundingAnalyzer | None = None,
        open_interest_analyzer: OpenInterestAnalyzer | None = None,
        liquidation_analyzer: LiquidationAnalyzer | None = None,
        score_calculator: FinalScoreCalculator | None = None,
        derivatives_client: DerivativesClient | None = None,
        fear_greed_client: FearGreedClient | None = None,
        dominance_client: DominanceClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._indicators = indicator_engine or IndicatorEngine(
            min_candles=self._settings.min_candles_required
        )
        tfs = tuple(
            tf.strip()
            for tf in getattr(self._settings, "market_regime_btc_timeframes", "1h,4h,1d,1w").split(",")
            if tf.strip()
        ) or DEFAULT_BTC_TIMEFRAMES
        self._btc = bitcoin_analyzer or BitcoinAnalyzer(
            timeframes=tfs, indicator_engine=self._indicators
        )
        self._eth = ethereum_analyzer or EthereumAnalyzer(indicator_engine=self._indicators)
        self._owns_deriv = derivatives_client is None
        self._deriv = derivatives_client or DerivativesClient(self._settings)
        self._owns_fg = fear_greed_client is None
        self._fg_client = fear_greed_client or FearGreedClient(self._settings)
        self._owns_dom = dominance_client is None
        self._dom_client = dominance_client or DominanceClient(self._settings)

        self._dominance = dominance_analyzer or DominanceAnalyzer(self._dom_client)
        self._fear_greed = fear_greed_analyzer or FearGreedAnalyzer(self._fg_client)
        self._funding = funding_analyzer or FundingAnalyzer(self._deriv)
        self._oi = open_interest_analyzer or OpenInterestAnalyzer(self._deriv)
        self._liquidity_fetcher = FreeVenueLiquidityFetcher(
            self._settings, binance=self._deriv
        )
        self._liqs = liquidation_analyzer or LiquidationAnalyzer(
            self._settings,
            fetcher=self._liquidity_fetcher,
            binance=self._deriv,
        )
        weights = ScoreWeights(
            coin=float(getattr(self._settings, "market_score_weight_coin", 0.60)),
            global_market=float(getattr(self._settings, "market_score_weight_global", 0.25)),
            funding=float(getattr(self._settings, "market_score_weight_funding", 0.05)),
            open_interest=float(getattr(self._settings, "market_score_weight_oi", 0.05)),
            liquidations=float(getattr(self._settings, "market_score_weight_liquidations", 0.05)),
        )
        self._scores = score_calculator or FinalScoreCalculator(weights)
        self._cache: MarketRegimeSnapshot | None = None

    @property
    def score_calculator(self) -> FinalScoreCalculator:
        return self._scores

    def clear_cache(self) -> None:
        self._cache = None

    async def close(self) -> None:
        if self._owns_fg:
            await self._fg_client.close()
        if self._owns_dom:
            await self._dom_client.close()
        await self._liqs.close()
        await self._liquidity_fetcher.close()
        if self._owns_deriv:
            await self._deriv.close()

    async def resolve(
        self,
        provider: MarketDataProvider,
        *,
        symbol: str | None = None,
        refresh: bool = False,
        include_aux: bool = True,
    ) -> MarketRegimeSnapshot:
        """Fetch BTC(/ETH) candles + aux sources and return a snapshot."""
        if self._cache is not None and not refresh:
            return self._cache

        # Tests must never hit external APIs (CoinGecko/Binance futures/F&G).
        if include_aux and self._settings.app_env == "test":
            include_aux = False

        btc_symbol = self._settings.regime_btc_symbol.upper()
        trade_symbol = (symbol or btc_symbol).upper()
        timeframes = list(self._btc.timeframes)
        btc_frames: dict[str, pd.DataFrame] = {}
        try:
            series_map = await provider.get_multi_timeframe_candles(
                btc_symbol,
                timeframes,
                limit=self._settings.candle_limit,
            )
            for tf, series in series_map.items():
                if series is not None and not series.is_empty:
                    btc_frames[tf] = series.to_dataframe()
        except Exception as exc:  # noqa: BLE001
            logger.warning("market_regime_btc_fetch_failed", error=str(exc))
            snap = empty_snapshot(utc_now(), detail=f"btc_fetch_error:{exc}")
            self._cache = snap
            return snap

        btc = self._btc.analyze_from_frames(btc_frames, symbol=btc_symbol)
        eth = await self._analyze_eth(provider, btc_frames) if include_aux else self._eth.analyze({})

        funding = (
            await self._funding.analyze(trade_symbol, btc_symbol=btc_symbol)
            if include_aux and getattr(self._settings, "market_regime_funding_enabled", True)
            else self._funding_disabled()
        )
        fear = (
            await self._fear_greed.analyze()
            if include_aux and getattr(self._settings, "market_regime_fear_greed_enabled", True)
            else self._fear_disabled()
        )
        dominance = (
            await self._dominance.analyze()
            if include_aux and getattr(self._settings, "market_regime_dominance_enabled", True)
            else self._dom_disabled()
        )

        btc_pref = btc_frames.get("1h")
        if btc_pref is None:
            btc_pref = btc_frames.get("4h")
        price_chg = _frame_change_pct(btc_pref)
        oi = (
            await self._oi.analyze(
                trade_symbol, btc_symbol=btc_symbol, price_change_pct=price_chg
            )
            if include_aux and getattr(self._settings, "market_regime_oi_enabled", True)
            else self._oi_disabled()
        )
        liqs = (
            await self._liqs.analyze(
                symbol=trade_symbol,
                btc_frame=btc_pref,
                oi_change_pct=(
                    oi.symbol_oi_change_pct if oi.available else None
                ),
            )
            if include_aux and getattr(self._settings, "market_regime_liquidations_enabled", True)
            else self._liq_disabled()
        )

        global_score = _aggregate_global(btc, eth, dominance, fear)
        bias = bias_from_score(global_score) if btc.available else MarketBias.NEUTRAL
        # Prefer BTC bias when it is the dominant component.
        if btc.available:
            bias = btc.bias if abs(btc.score) >= abs(global_score) * 0.6 else bias_from_score(global_score)

        snap = MarketRegimeSnapshot(
            available=btc.available,
            bias=bias,
            btc=btc,
            eth=eth,
            dominance=dominance,
            fear_greed=fear,
            funding=funding,
            open_interest=oi,
            liquidations=liqs,
            global_score=round(global_score, 2),
            captured_at=utc_now(),
            detail=(
                f"bias={bias.value} global={global_score:.1f} "
                f"btc={btc.detail}; eth={eth.detail}; dom={dominance.detail}; "
                f"fng={fear.detail}; fund={funding.detail}"
            ),
        )
        self._cache = snap
        logger.info(
            "market_regime_resolved",
            bias=snap.bias.value,
            available=snap.available,
            global_score=snap.global_score,
            btc_bias=snap.btc.bias.value if snap.btc.available else None,
        )
        return snap

    def resolve_from_btc_frames(
        self,
        btc_frames: Mapping[str, pd.DataFrame],
        *,
        eth_frames: Mapping[str, pd.DataFrame] | None = None,
    ) -> MarketRegimeSnapshot:
        """Sync path for backtests (BTC/ETH frames only; aux modules unavailable)."""
        btc = self._btc.analyze_from_frames(btc_frames, symbol=self._settings.regime_btc_symbol.upper())
        eth = self._eth.analyze(eth_frames or {}, btc_frames)
        global_score = _aggregate_global(btc, eth, self._dom_disabled(), self._fear_disabled())
        bias = btc.bias if btc.available else MarketBias.NEUTRAL
        return MarketRegimeSnapshot(
            available=btc.available,
            bias=bias,
            btc=btc,
            eth=eth,
            dominance=self._dom_disabled(),
            fear_greed=self._fear_disabled(),
            funding=self._funding_disabled(),
            open_interest=self._oi_disabled(),
            liquidations=self._liq_disabled(),
            global_score=round(global_score, 2),
            captured_at=utc_now(),
            detail=f"backtest_btc bias={bias.value} score={global_score:.1f}",
        )

    async def _analyze_eth(
        self,
        provider: MarketDataProvider,
        btc_frames: Mapping[str, pd.DataFrame],
    ):
        if not getattr(self._settings, "market_regime_eth_enabled", True):
            return self._eth.analyze({})
        eth_symbol = getattr(self._settings, "market_regime_eth_symbol", "ETHUSDT").upper()
        tfs = [tf for tf in ("1h", "4h", "1d") if tf in self._btc.timeframes or True]
        try:
            series_map = await provider.get_multi_timeframe_candles(
                eth_symbol, tfs, limit=self._settings.candle_limit
            )
            eth_frames = {
                tf: s.to_dataframe()
                for tf, s in series_map.items()
                if s is not None and not s.is_empty
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("market_regime_eth_fetch_failed", error=str(exc))
            return self._eth.analyze({})
        return self._eth.analyze(eth_frames, btc_frames, symbol=eth_symbol)

    def _funding_disabled(self):
        from app.market_regime.types import FundingAnalysis

        return FundingAnalysis(available=False, detail="funding_disabled")

    def _fear_disabled(self):
        from app.market_regime.types import FearGreedAnalysis

        return FearGreedAnalysis(available=False, detail="fear_greed_disabled")

    def _dom_disabled(self):
        from app.market_regime.types import DominanceAnalysis

        return DominanceAnalysis(available=False, detail="dominance_disabled")

    def _oi_disabled(self):
        from app.market_regime.types import OpenInterestAnalysis

        return OpenInterestAnalysis(available=False, detail="oi_disabled")

    def _liq_disabled(self):
        from app.market_regime.types import LiquidationAnalysis

        return LiquidationAnalysis(available=False, detail="liquidations_disabled")


def _aggregate_global(btc, eth, dominance, fear) -> float:
    parts: list[tuple[float, float]] = []
    if btc.available:
        parts.append((btc.score, 0.55))
    if eth.available:
        parts.append((eth.score, 0.15))
    if dominance.available:
        parts.append((dominance.score, 0.20))
    if fear.available:
        parts.append((fear.score, 0.10))
    if not parts:
        return 0.0
    total_w = sum(w for _, w in parts)
    return sum(score * w for score, w in parts) / total_w


def _frame_change_pct(frame: pd.DataFrame | None, lookback: int = 6) -> float | None:
    if frame is None or "close" not in getattr(frame, "columns", []):
        return None
    if len(frame) < lookback + 1:
        return None
    a = float(frame["close"].iloc[-lookback])
    b = float(frame["close"].iloc[-1])
    if a <= 0:
        return None
    return (b / a - 1.0) * 100.0
