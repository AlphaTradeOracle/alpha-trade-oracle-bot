"""Liquidation / liquidity analyzer — free venues first.

Primary: Binance Futures + Bybit + Hyperliquid → own Liquidity Score.
Optional: ``LIQUIDATION_API_URL`` for later paid feeds (CoinGlass/Hyblock/…).
Fallback: candle wick heuristic when venues are unreachable.
"""

from __future__ import annotations

from typing import Any

import httpx
import pandas as pd

from app.core.config import Settings, get_settings
from app.core.http import request_with_retry
from app.core.logging import get_logger
from app.market_regime.liquidity import (
    FreeVenueLiquidityFetcher,
    LiquidityScoreCalculator,
    LiquidityScoreResult,
)
from app.market_regime.sources import BybitPublicClient, DerivativesClient, HyperliquidPublicClient
from app.market_regime.types import LiquidationAnalysis

logger = get_logger(__name__)


class LiquidationAnalyzer:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        fetcher: FreeVenueLiquidityFetcher | None = None,
        calculator: LiquidityScoreCalculator | None = None,
        binance: DerivativesClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_http = client is None
        self._http = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
            headers={"User-Agent": "alpha-trade-oracle-bot/0.1"},
        )
        self._calculator = calculator or LiquidityScoreCalculator()
        if fetcher is not None:
            self._fetcher = fetcher
            self._owns_fetcher = False
        else:
            self._fetcher = FreeVenueLiquidityFetcher(
                self._settings,
                binance=binance,
                bybit=BybitPublicClient(self._settings),
                hyperliquid=HyperliquidPublicClient(self._settings),
            )
            self._owns_fetcher = True

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
        if self._owns_fetcher:
            await self._fetcher.close()

    async def analyze(
        self,
        *,
        symbol: str = "BTCUSDT",
        btc_frame: pd.DataFrame | None = None,
        oi_change_pct: float | None = None,
    ) -> LiquidationAnalysis:
        """Build liquidation/liquidity score from free venues, then optional feed."""
        free = await self._from_free_venues(
            symbol, btc_frame=btc_frame, oi_change_pct=oi_change_pct
        )
        if free.available:
            return free

        paid = await self._from_optional_feed()
        if paid.available:
            return paid

        if btc_frame is not None and len(btc_frame) >= 30:
            return self._from_wick_only(btc_frame)
        return LiquidationAnalysis(available=False, detail="liquidations_unavailable")

    async def _from_free_venues(
        self,
        symbol: str,
        *,
        btc_frame: pd.DataFrame | None,
        oi_change_pct: float | None,
    ) -> LiquidationAnalysis:
        try:
            snaps = await self._fetcher.fetch(symbol)
            # Prefer BTC bundle as market-liquidity anchor when symbol differs.
            btc_symbol = self._settings.regime_btc_symbol.upper()
            if symbol.upper() != btc_symbol:
                btc_snaps = await self._fetcher.fetch(btc_symbol)
                # Merge unique venues; coin-specific first, then BTC fillers.
                seen = {s.venue for s in snaps}
                for snap in btc_snaps:
                    if snap.venue not in seen:
                        snaps.append(snap)
            result = self._calculator.compute(
                snaps, btc_frame=btc_frame, oi_change_pct=oi_change_pct
            )
            return _from_liquidity_result(result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("free_liquidity_failed", symbol=symbol, error=str(exc))
            return LiquidationAnalysis(available=False, detail=f"free_liquidity_error:{exc}")

    async def _from_optional_feed(self) -> LiquidationAnalysis:
        url = (getattr(self._settings, "liquidation_api_url", "") or "").strip()
        if not url:
            return LiquidationAnalysis(available=False, detail="liquidation_api_unset")
        try:
            response = await request_with_retry(
                self._http,
                "GET",
                url,
                max_retries=min(2, self._settings.http_max_retries),
            )
            response.raise_for_status()
            payload: Any = response.json()
            long_usd = _num(payload, "longLiquidationsUsd", "long_usd", "long")
            short_usd = _num(payload, "shortLiquidationsUsd", "short_usd", "short")
            if long_usd is None and short_usd is None:
                return LiquidationAnalysis(available=False, detail="liquidation_feed_empty")
            score = _imbalance_score(long_usd or 0.0, short_usd or 0.0)
            return LiquidationAnalysis(
                available=True,
                long_liquidations_usd=long_usd,
                short_liquidations_usd=short_usd,
                score=score,
                source="paid_feed",
                detail=f"liquidation_feed long={long_usd} short={short_usd}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("liquidation_feed_failed", error=str(exc))
            return LiquidationAnalysis(available=False, detail=f"liquidation_feed_error:{exc}")

    def _from_wick_only(self, frame: pd.DataFrame) -> LiquidationAnalysis:
        result = self._calculator.compute([], btc_frame=frame)
        analysis = _from_liquidity_result(result)
        if analysis.available:
            return LiquidationAnalysis(
                available=True,
                long_liquidations_usd=result.wick_long_pressure,
                short_liquidations_usd=result.wick_short_pressure,
                score=analysis.score,
                liquidity_score=result.score,
                venues=tuple(result.venues),
                source="wick_heuristic",
                detail=result.detail,
                extras=result.to_dict(),
            )
        return LiquidationAnalysis(available=False, detail="liquidation_heuristic_unavailable")


def _from_liquidity_result(result: LiquidityScoreResult) -> LiquidationAnalysis:
    if not result.available:
        return LiquidationAnalysis(available=False, detail=result.detail)
    return LiquidationAnalysis(
        available=True,
        long_liquidations_usd=result.wick_long_pressure,
        short_liquidations_usd=result.wick_short_pressure,
        score=result.score,
        liquidity_score=result.score,
        venues=tuple(result.venues),
        long_share=result.avg_long_share,
        book_imbalance=result.avg_book_imbalance,
        avg_funding=result.avg_funding,
        source="free_venues",
        detail=result.detail,
        extras=result.to_dict(),
    )


def _num(payload: Any, *keys: str) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        if key in payload and payload[key] is not None:
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                continue
    data = payload.get("data")
    if isinstance(data, dict):
        return _num(data, *keys)
    return None


def _imbalance_score(long_usd: float, short_usd: float) -> float:
    total = long_usd + short_usd
    if total <= 0:
        return 0.0
    imbalance = (long_usd - short_usd) / total
    return round(max(-100.0, min(100.0, imbalance * 50.0)), 2)
