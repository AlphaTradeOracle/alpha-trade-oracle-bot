"""HTTP clients for market-regime external data (funding, OI, F&G, dominance)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.http import request_with_retry
from app.core.logging import get_logger
from app.core.time import utc_now

logger = get_logger(__name__)

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
FEAR_GREED_URL = "https://api.alternative.me/fng/"


@dataclass(frozen=True)
class FundingReading:
    symbol: str
    rate: float
    mark_price: float | None
    next_funding_time: datetime | None
    history: tuple[float, ...] = ()


@dataclass(frozen=True)
class OpenInterestReading:
    symbol: str
    open_interest: float
    history: tuple[float, ...] = ()


@dataclass(frozen=True)
class LongShortReading:
    """Account / position long-short ratio (longShare in 0..1)."""

    venue: str
    symbol: str
    long_share: float
    short_share: float


@dataclass(frozen=True)
class OrderBookReading:
    venue: str
    symbol: str
    bid_notional: float
    ask_notional: float

    @property
    def imbalance(self) -> float:
        """+1 = all bids, -1 = all asks."""
        total = self.bid_notional + self.ask_notional
        if total <= 0:
            return 0.0
        return (self.bid_notional - self.ask_notional) / total


@dataclass(frozen=True)
class VenueTickerReading:
    venue: str
    symbol: str
    price: float | None
    volume_24h: float | None
    funding_rate: float | None = None
    open_interest: float | None = None


@dataclass(frozen=True)
class FearGreedReading:
    value: int
    classification: str
    timestamp: datetime


@dataclass(frozen=True)
class DominanceReading:
    btc_dominance: float
    eth_dominance: float
    usdt_dominance: float | None
    total_market_cap: float | None
    alt_market_cap: float | None  # TOTAL3 proxy: total - btc - eth


class DerivativesClient:
    """Binance USDT-M futures public endpoints for funding + open interest."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = BINANCE_FUTURES_BASE,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns = client is None
        resolved = (
            base_url
            if base_url != BINANCE_FUTURES_BASE
            else getattr(self._settings, "binance_futures_base_url", BINANCE_FUTURES_BASE)
        )
        self._client = client or httpx.AsyncClient(
            base_url=resolved.rstrip("/"),
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
            headers={"User-Agent": "alpha-trade-oracle-bot/0.1"},
        )

    async def close(self) -> None:
        if self._owns:
            await self._client.aclose()

    async def fetch_funding(self, symbol: str, *, history_limit: int = 24) -> FundingReading | None:
        sym = symbol.upper().strip()
        try:
            premium = await self._get("/fapi/v1/premiumIndex", {"symbol": sym})
            if not isinstance(premium, dict) or "lastFundingRate" not in premium:
                return None
            rate = float(premium["lastFundingRate"])
            mark = float(premium["markPrice"]) if premium.get("markPrice") is not None else None
            nft = None
            if premium.get("nextFundingTime") is not None:
                nft = datetime.fromtimestamp(float(premium["nextFundingTime"]) / 1000.0)
            history: list[float] = []
            hist = await self._get(
                "/fapi/v1/fundingRate",
                {"symbol": sym, "limit": max(1, min(history_limit, 100))},
            )
            if isinstance(hist, list):
                for row in hist:
                    try:
                        history.append(float(row["fundingRate"]))
                    except (KeyError, TypeError, ValueError):
                        continue
            return FundingReading(sym, rate, mark, nft, tuple(history))
        except Exception as exc:  # noqa: BLE001
            logger.warning("funding_fetch_failed", symbol=sym, error=str(exc))
            return None

    async def fetch_open_interest(
        self, symbol: str, *, hist_limit: int = 12
    ) -> OpenInterestReading | None:
        sym = symbol.upper().strip()
        try:
            payload = await self._get("/fapi/v1/openInterest", {"symbol": sym})
            if not isinstance(payload, dict) or "openInterest" not in payload:
                return None
            oi = float(payload["openInterest"])
            history: list[float] = []
            hist = await self._get(
                "/futures/data/openInterestHist",
                {"symbol": sym, "period": "1h", "limit": max(1, min(hist_limit, 30))},
            )
            if isinstance(hist, list):
                for row in hist:
                    try:
                        history.append(float(row["sumOpenInterest"]))
                    except (KeyError, TypeError, ValueError):
                        continue
            return OpenInterestReading(sym, oi, tuple(history))
        except Exception as exc:  # noqa: BLE001
            logger.warning("oi_fetch_failed", symbol=sym, error=str(exc))
            return None

    async def fetch_long_short_ratio(self, symbol: str) -> LongShortReading | None:
        """Global account long/short ratio (Binance futures data endpoint)."""
        sym = symbol.upper().strip()
        try:
            payload = await self._get(
                "/futures/data/globalLongShortAccountRatio",
                {"symbol": sym, "period": "1h", "limit": 1},
            )
            if not isinstance(payload, list) or not payload:
                return None
            row = payload[-1]
            long_share = float(row["longAccount"])
            short_share = float(row["shortAccount"])
            return LongShortReading("binance", sym, long_share, short_share)
        except Exception as exc:  # noqa: BLE001
            logger.warning("binance_long_short_failed", symbol=sym, error=str(exc))
            return None

    async def fetch_order_book(self, symbol: str, *, limit: int = 50) -> OrderBookReading | None:
        sym = symbol.upper().strip()
        try:
            payload = await self._get("/fapi/v1/depth", {"symbol": sym, "limit": limit})
            if not isinstance(payload, dict):
                return None
            bids = payload.get("bids") or []
            asks = payload.get("asks") or []
            bid_n = sum(float(p) * float(q) for p, q, *_ in bids)
            ask_n = sum(float(p) * float(q) for p, q, *_ in asks)
            return OrderBookReading("binance", sym, bid_n, ask_n)
        except Exception as exc:  # noqa: BLE001
            logger.warning("binance_depth_failed", symbol=sym, error=str(exc))
            return None

    async def fetch_ticker(self, symbol: str) -> VenueTickerReading | None:
        sym = symbol.upper().strip()
        try:
            payload = await self._get("/fapi/v1/ticker/24hr", {"symbol": sym})
            if not isinstance(payload, dict):
                return None
            return VenueTickerReading(
                venue="binance",
                symbol=sym,
                price=float(payload["lastPrice"]) if payload.get("lastPrice") is not None else None,
                volume_24h=(
                    float(payload["quoteVolume"]) if payload.get("quoteVolume") is not None else None
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("binance_ticker_failed", symbol=sym, error=str(exc))
            return None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await request_with_retry(
            self._client,
            "GET",
            path,
            params=params or {},
            max_retries=min(2, self._settings.http_max_retries),
        )
        response.raise_for_status()
        return response.json()


class BybitPublicClient:
    """Bybit v5 public linear endpoints (funding, OI, L/S, orderbook, ticker)."""

    BASE = "https://api.bybit.com"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.BASE,
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
            headers={"User-Agent": "alpha-trade-oracle-bot/0.1"},
        )

    async def close(self) -> None:
        if self._owns:
            await self._client.aclose()

    async def fetch_bundle(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper().strip()
        out: dict[str, Any] = {"venue": "bybit", "symbol": sym}
        try:
            ticker = await self._get(
                "/v5/market/tickers", {"category": "linear", "symbol": sym}
            )
            rows = ((ticker or {}).get("result") or {}).get("list") or []
            if rows:
                row = rows[0]
                out["ticker"] = VenueTickerReading(
                    venue="bybit",
                    symbol=sym,
                    price=_f(row.get("lastPrice")),
                    volume_24h=_f(row.get("turnover24h")),
                    funding_rate=_f(row.get("fundingRate")),
                    open_interest=_f(row.get("openInterest")),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("bybit_ticker_failed", symbol=sym, error=str(exc))

        try:
            ratio = await self._get(
                "/v5/market/account-ratio",
                {"category": "linear", "symbol": sym, "period": "1h", "limit": 1},
            )
            rows = ((ratio or {}).get("result") or {}).get("list") or []
            if rows:
                row = rows[0]
                buy = _f(row.get("buyRatio"))
                sell = _f(row.get("sellRatio"))
                if buy is not None and sell is not None:
                    total = buy + sell
                    if total > 0:
                        out["long_short"] = LongShortReading(
                            "bybit", sym, buy / total, sell / total
                        )
        except Exception as exc:  # noqa: BLE001
            logger.warning("bybit_account_ratio_failed", symbol=sym, error=str(exc))

        try:
            book = await self._get(
                "/v5/market/orderbook",
                {"category": "linear", "symbol": sym, "limit": 50},
            )
            result = (book or {}).get("result") or {}
            bids = result.get("b") or []
            asks = result.get("a") or []
            bid_n = sum(float(p) * float(q) for p, q, *_ in bids)
            ask_n = sum(float(p) * float(q) for p, q, *_ in asks)
            out["order_book"] = OrderBookReading("bybit", sym, bid_n, ask_n)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bybit_orderbook_failed", symbol=sym, error=str(exc))

        return out

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = await request_with_retry(
            self._client,
            "GET",
            path,
            params=params,
            max_retries=min(2, self._settings.http_max_retries),
        )
        response.raise_for_status()
        return response.json()


class HyperliquidPublicClient:
    """Hyperliquid public info endpoints (funding, OI, L2 book)."""

    BASE = "https://api.hyperliquid.xyz"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.BASE,
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
            headers={"User-Agent": "alpha-trade-oracle-bot/0.1", "Content-Type": "application/json"},
        )

    async def close(self) -> None:
        if self._owns:
            await self._client.aclose()

    async def fetch_bundle(self, symbol: str) -> dict[str, Any]:
        coin = _hyperliquid_coin(symbol)
        out: dict[str, Any] = {"venue": "hyperliquid", "symbol": symbol.upper(), "coin": coin}
        try:
            meta = await self._post({"type": "metaAndAssetCtxs"})
            if isinstance(meta, list) and len(meta) >= 2:
                universe = (meta[0] or {}).get("universe") or []
                ctxs = meta[1] or []
                idx = next(
                    (i for i, a in enumerate(universe) if str(a.get("name", "")).upper() == coin),
                    None,
                )
                if idx is not None and idx < len(ctxs):
                    ctx = ctxs[idx]
                    out["ticker"] = VenueTickerReading(
                        venue="hyperliquid",
                        symbol=symbol.upper(),
                        price=_f(ctx.get("markPx")),
                        volume_24h=_f(ctx.get("dayNtlVlm")),
                        funding_rate=_f(ctx.get("funding")),
                        open_interest=_f(ctx.get("openInterest")),
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("hyperliquid_meta_failed", coin=coin, error=str(exc))

        try:
            book = await self._post({"type": "l2Book", "coin": coin})
            levels = (book or {}).get("levels") or []
            bids = levels[0] if len(levels) > 0 else []
            asks = levels[1] if len(levels) > 1 else []
            bid_n = sum(float(x.get("px", 0)) * float(x.get("sz", 0)) for x in bids[:50])
            ask_n = sum(float(x.get("px", 0)) * float(x.get("sz", 0)) for x in asks[:50])
            out["order_book"] = OrderBookReading("hyperliquid", symbol.upper(), bid_n, ask_n)
        except Exception as exc:  # noqa: BLE001
            logger.warning("hyperliquid_book_failed", coin=coin, error=str(exc))

        return out

    async def _post(self, body: dict[str, Any]) -> Any:
        response = await request_with_retry(
            self._client,
            "POST",
            "/info",
            json=body,
            max_retries=min(2, self._settings.http_max_retries),
        )
        response.raise_for_status()
        return response.json()


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hyperliquid_coin(symbol: str) -> str:
    sym = symbol.upper().strip()
    for quote in ("USDT", "USDC", "USD"):
        if sym.endswith(quote) and len(sym) > len(quote):
            return sym[: -len(quote)]
    return sym


class FearGreedClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
            headers={"User-Agent": "alpha-trade-oracle-bot/0.1"},
        )

    async def close(self) -> None:
        if self._owns:
            await self._client.aclose()

    async def fetch(self) -> FearGreedReading | None:
        try:
            response = await request_with_retry(
                self._client,
                "GET",
                FEAR_GREED_URL,
                params={"limit": 1},
                max_retries=self._settings.http_max_retries,
            )
            response.raise_for_status()
            payload = response.json()
            data = (payload or {}).get("data") or []
            if not data:
                return None
            row = data[0]
            ts = utc_now()
            if row.get("timestamp") is not None:
                ts = datetime.fromtimestamp(float(row["timestamp"]))
            return FearGreedReading(
                value=int(row["value"]),
                classification=str(row.get("value_classification") or ""),
                timestamp=ts,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("fear_greed_fetch_failed", error=str(exc))
            return None


class DominanceClient:
    """CoinGecko /global + markets for BTC.D / USDT.D / TOTAL3 proxy."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns = client is None
        headers = {"User-Agent": "alpha-trade-oracle-bot/0.1", "Accept": "application/json"}
        api_key = self._settings.coingecko_api_key.get_secret_value().strip()
        if api_key:
            if "pro-api.coingecko.com" in self._settings.coingecko_base_url:
                headers["x-cg-pro-api-key"] = api_key
            else:
                headers["x-cg-demo-api-key"] = api_key
        self._client = client or httpx.AsyncClient(
            base_url=self._settings.coingecko_base_url.rstrip("/"),
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
            headers=headers,
        )

    async def close(self) -> None:
        if self._owns:
            await self._client.aclose()

    async def fetch(self) -> DominanceReading | None:
        try:
            response = await request_with_retry(
                self._client,
                "GET",
                "/global",
                max_retries=self._settings.http_max_retries,
            )
            response.raise_for_status()
            data = (response.json() or {}).get("data") or {}
            mcap_pct = data.get("market_cap_percentage") or {}
            btc_d = float(mcap_pct.get("btc") or 0.0)
            eth_d = float(mcap_pct.get("eth") or 0.0)
            total = None
            total_raw = (data.get("total_market_cap") or {}).get("usd")
            if total_raw is not None:
                total = float(total_raw)

            usdt_d = float(mcap_pct["usdt"]) if mcap_pct.get("usdt") is not None else None
            if usdt_d is None:
                usdt_d = await self._usdt_dominance_from_markets(total)

            alt = None
            if total is not None and btc_d > 0 and eth_d > 0:
                alt = total * (1.0 - (btc_d + eth_d) / 100.0)

            if btc_d <= 0:
                return None
            return DominanceReading(
                btc_dominance=btc_d,
                eth_dominance=eth_d,
                usdt_dominance=usdt_d,
                total_market_cap=total,
                alt_market_cap=alt,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dominance_fetch_failed", error=str(exc))
            return None

    async def _usdt_dominance_from_markets(self, total: float | None) -> float | None:
        if total is None or total <= 0:
            return None
        try:
            response = await request_with_retry(
                self._client,
                "GET",
                "/coins/markets",
                params={"vs_currency": "usd", "ids": "tether", "per_page": 1, "page": 1},
                max_retries=self._settings.http_max_retries,
            )
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list) or not rows:
                return None
            mcap = rows[0].get("market_cap")
            if mcap is None:
                return None
            return float(mcap) / total * 100.0
        except Exception:
            return None
