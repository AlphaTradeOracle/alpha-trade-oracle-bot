"""Free-venue Liquidity Score (Binance / Bybit / Hyperliquid).

No paid providers. Inputs: funding, open interest, long/short ratio,
order-book imbalance, 24h volume, optional candle wick pressure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.market_regime.sources import (
    BybitPublicClient,
    DerivativesClient,
    HyperliquidPublicClient,
    LongShortReading,
    OrderBookReading,
    VenueTickerReading,
)

logger = get_logger(__name__)

DEFAULT_VENUES = ("binance", "bybit", "hyperliquid")


@dataclass(frozen=True)
class VenueLiquiditySnapshot:
    venue: str
    price: float | None = None
    funding_rate: float | None = None
    open_interest: float | None = None
    long_share: float | None = None
    book_imbalance: float | None = None
    volume_24h: float | None = None


@dataclass
class LiquidityScoreResult:
    available: bool
    score: float = 0.0
    venues: list[str] = field(default_factory=list)
    avg_funding: float | None = None
    avg_long_share: float | None = None
    avg_book_imbalance: float | None = None
    oi_change_proxy: float | None = None
    volume_score: float | None = None
    wick_long_pressure: float | None = None
    wick_short_pressure: float | None = None
    components: dict[str, float] = field(default_factory=dict)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "score": round(self.score, 2),
            "venues": list(self.venues),
            "avgFunding": self.avg_funding,
            "avgLongShare": self.avg_long_share,
            "avgBookImbalance": self.avg_book_imbalance,
            "volumeScore": self.volume_score,
            "components": {k: round(v, 2) for k, v in self.components.items()},
            "detail": self.detail,
        }


class LiquidityScoreCalculator:
    """Combine free-venue microstructure into a directional liquidity score.

    Score is bullish-positive in [-100, +100]:
    - Crowded longs (high funding, high long share) → negative (caution for new longs)
    - Bid-heavy book / short flush wicks → positive
    """

    def compute(
        self,
        venue_snaps: list[VenueLiquiditySnapshot],
        *,
        btc_frame: pd.DataFrame | None = None,
        oi_change_pct: float | None = None,
    ) -> LiquidityScoreResult:
        if not venue_snaps and btc_frame is None:
            return LiquidityScoreResult(available=False, detail="liquidity_no_inputs")

        fundings = [v.funding_rate for v in venue_snaps if v.funding_rate is not None]
        longs = [v.long_share for v in venue_snaps if v.long_share is not None]
        books = [v.book_imbalance for v in venue_snaps if v.book_imbalance is not None]
        volumes = [v.volume_24h for v in venue_snaps if v.volume_24h is not None]

        components: dict[str, float] = {}
        weights: dict[str, float] = {}

        avg_funding = _mean(fundings)
        if avg_funding is not None:
            # 0.05% 8h funding ≈ crowded
            components["funding"] = max(-100.0, min(100.0, -avg_funding / 0.0005 * 50.0))
            weights["funding"] = 0.30

        avg_long = _mean(longs)
        if avg_long is not None:
            # >0.55 long-heavy → negative for new longs
            components["long_short"] = max(-100.0, min(100.0, (0.50 - avg_long) * 400.0))
            weights["long_short"] = 0.25

        avg_book = _mean(books)
        if avg_book is not None:
            components["order_book"] = max(-100.0, min(100.0, avg_book * 100.0))
            weights["order_book"] = 0.20

        vol_score = None
        if volumes:
            # Relative volume across venues is not comparable; use presence as mild
            # activity confirmation only when wick/OI also speak.
            vol_score = min(100.0, (sum(volumes) / max(len(volumes), 1)) / 1e9 * 10.0)
            components["volume"] = max(-20.0, min(20.0, vol_score - 10.0))
            weights["volume"] = 0.10

        if oi_change_pct is not None:
            # Rising OI with crowded longs is more fragile; bare OI rise is mild risk-on.
            components["oi"] = max(-100.0, min(100.0, oi_change_pct * 5.0))
            weights["oi"] = 0.10

        wick_long = wick_short = None
        if btc_frame is not None and len(btc_frame) >= 24:
            wick_long, wick_short = _wick_pressure(btc_frame)
            total_w = float(wick_long) + float(wick_short)
            if total_w > 0:
                # Long liquidations (lower wicks) → short-term long-friendly flush
                components["wick"] = max(
                    -100.0,
                    min(100.0, (float(wick_long) - float(wick_short)) / total_w * 50.0),
                )
                weights["wick"] = 0.15 if venue_snaps else 1.0

        if not components:
            return LiquidityScoreResult(available=False, detail="liquidity_no_components")

        wsum = sum(weights.values())
        score = sum(components[k] * weights[k] for k in components) / wsum
        venues = sorted({v.venue for v in venue_snaps})
        return LiquidityScoreResult(
            available=True,
            score=round(score, 2),
            venues=venues,
            avg_funding=None if avg_funding is None else round(avg_funding, 8),
            avg_long_share=None if avg_long is None else round(avg_long, 4),
            avg_book_imbalance=None if avg_book is None else round(avg_book, 4),
            oi_change_proxy=oi_change_pct,
            volume_score=None if vol_score is None else round(vol_score, 2),
            wick_long_pressure=wick_long,
            wick_short_pressure=wick_short,
            components=components,
            detail=(
                f"liquidity_score={score:.1f} venues={','.join(venues) or 'candle'} "
                f"comps={','.join(components)}"
            ),
        )


class FreeVenueLiquidityFetcher:
    """Pull free microstructure from Binance, Bybit, Hyperliquid."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        binance: DerivativesClient | None = None,
        bybit: BybitPublicClient | None = None,
        hyperliquid: HyperliquidPublicClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_binance = binance is None
        self._binance = binance or DerivativesClient(self._settings)
        self._owns_bybit = bybit is None
        self._bybit = bybit or BybitPublicClient(self._settings)
        self._owns_hl = hyperliquid is None
        self._hl = hyperliquid or HyperliquidPublicClient(self._settings)

    async def close(self) -> None:
        if self._owns_binance:
            await self._binance.close()
        if self._owns_bybit:
            await self._bybit.close()
        if self._owns_hl:
            await self._hl.close()

    def enabled_venues(self) -> list[str]:
        raw = getattr(
            self._settings, "market_regime_liquidity_venues", "binance,bybit,hyperliquid"
        )
        return [v.strip().lower() for v in str(raw).split(",") if v.strip()]

    async def fetch(self, symbol: str) -> list[VenueLiquiditySnapshot]:
        # Unit/integration tests must stay offline.
        if self._settings.app_env == "test":
            return []
        venues = self.enabled_venues() or list(DEFAULT_VENUES)
        snaps: list[VenueLiquiditySnapshot] = []
        if "binance" in venues:
            snap = await self._fetch_binance(symbol)
            if snap is not None:
                snaps.append(snap)
        if "bybit" in venues:
            snap = await self._fetch_bybit(symbol)
            if snap is not None:
                snaps.append(snap)
        if "hyperliquid" in venues:
            snap = await self._fetch_hyperliquid(symbol)
            if snap is not None:
                snaps.append(snap)
        return snaps

    async def _fetch_binance(self, symbol: str) -> VenueLiquiditySnapshot | None:
        ticker: VenueTickerReading | None = await self._binance.fetch_ticker(symbol)
        funding = await self._binance.fetch_funding(symbol, history_limit=3)
        oi = await self._binance.fetch_open_interest(symbol, hist_limit=3)
        ls: LongShortReading | None = await self._binance.fetch_long_short_ratio(symbol)
        book: OrderBookReading | None = await self._binance.fetch_order_book(symbol)
        if all(x is None for x in (ticker, funding, oi, ls, book)):
            return None
        return VenueLiquiditySnapshot(
            venue="binance",
            price=ticker.price if ticker else None,
            funding_rate=funding.rate if funding else None,
            open_interest=oi.open_interest if oi else None,
            long_share=ls.long_share if ls else None,
            book_imbalance=book.imbalance if book else None,
            volume_24h=ticker.volume_24h if ticker else None,
        )

    async def _fetch_bybit(self, symbol: str) -> VenueLiquiditySnapshot | None:
        bundle = await self._bybit.fetch_bundle(symbol)
        ticker: VenueTickerReading | None = bundle.get("ticker")
        ls: LongShortReading | None = bundle.get("long_short")
        book: OrderBookReading | None = bundle.get("order_book")
        if ticker is None and ls is None and book is None:
            return None
        return VenueLiquiditySnapshot(
            venue="bybit",
            price=ticker.price if ticker else None,
            funding_rate=ticker.funding_rate if ticker else None,
            open_interest=ticker.open_interest if ticker else None,
            long_share=ls.long_share if ls else None,
            book_imbalance=book.imbalance if book else None,
            volume_24h=ticker.volume_24h if ticker else None,
        )

    async def _fetch_hyperliquid(self, symbol: str) -> VenueLiquiditySnapshot | None:
        bundle = await self._hl.fetch_bundle(symbol)
        ticker: VenueTickerReading | None = bundle.get("ticker")
        book: OrderBookReading | None = bundle.get("order_book")
        if ticker is None and book is None:
            return None
        return VenueLiquiditySnapshot(
            venue="hyperliquid",
            price=ticker.price if ticker else None,
            funding_rate=ticker.funding_rate if ticker else None,
            open_interest=ticker.open_interest if ticker else None,
            long_share=None,
            book_imbalance=book.imbalance if book else None,
            volume_24h=ticker.volume_24h if ticker else None,
        )


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _wick_pressure(frame: pd.DataFrame) -> tuple[float, float]:
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(set(frame.columns)):
        return 0.0, 0.0
    tail = frame.iloc[-24:]
    long_p = short_p = 0.0
    opens = tail["open"].astype(float).to_numpy()
    highs = tail["high"].astype(float).to_numpy()
    lows = tail["low"].astype(float).to_numpy()
    closes = tail["close"].astype(float).to_numpy()
    volumes = tail["volume"].astype(float).to_numpy()
    for o, h, l, c, v in zip(opens, highs, lows, closes, volumes, strict=True):
        rng = max(h - l, 1e-12)
        lower = min(o, c) - l
        upper = h - max(o, c)
        if lower / rng > 0.55 and v > 0:
            long_p += v * (lower / rng)
        if upper / rng > 0.55 and v > 0:
            short_p += v * (upper / rng)
    return round(long_p, 2), round(short_p, 2)
