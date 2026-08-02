"""CoinGecko-Ranking fuer das Market-Cap-Universe.

Kein vollstaendiger :class:`~app.market_data.base.MarketDataProvider` — nur
Top-N-Maerkte nach Marktkapitalisierung. Kerzen kommen weiterhin von Binance/KuCoin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import MarketDataError
from app.core.http import RateLimiter, request_with_retry
from app.core.logging import get_logger

logger = get_logger(__name__)

#: CoinGecko erlaubt maximal 250 Eintraege pro ``/coins/markets``-Seite.
MAX_PER_PAGE = 250

#: Konservativ fuer den kostenlosen / Demo-Plan.
RATE_LIMIT_CALLS = 25
RATE_LIMIT_PERIOD_SECONDS = 60.0

#: CoinGecko ``market.identifier`` / ``market.name`` auf unsere Provider-Namen.
EXCHANGE_IDENTIFIER_ALIASES: dict[str, frozenset[str]] = {
    "kucoin": frozenset({"kucoin", "kucoin_exchange"}),
    "binance": frozenset({"binance", "binance_us"}),
    "coinbase": frozenset({"coinbase", "gdax", "coinbase_exchange"}),
}

#: Wrapped / bridged / RWA / exchange-oddities die CoinGecko in die Top-N
#: schiebt, aber kein sinnvolles Desk-Banner „Top Coin“ sind.
_DESK_BANNER_EXCLUDED_IDS = frozenset(
    {
        "staked-ether",
        "wrapped-steth",
        "wrapped-bitcoin",
        "weth",
        "weeth",
        "coinbase-wrapped-btc",
        "tbtc",
        "binance-bridged-usdt-bnb-smart-chain",
        "binance-bridged-usdc-bnb-smart-chain",
        "figure-heloc",
        "whitebit",
        "leo-token",
        "rain",
        "ethena-usde",
        "susds",
        "usds",
        "ethena-staked-usde",
        "kelp-dao-restaked-eth",
        "renzo-restaked-eth",
        "mantle-staked-ether",
        "liquid-staked-ethereum",
        "polygon-bridged-usdt-polygon",
    }
)
_DESK_BANNER_EXCLUDED_SYMBOLS = frozenset(
    {
        "WETH",
        "WBTC",
        "STETH",
        "WSTETH",
        "WEETH",
        "CBBTC",
        "TBTC",
        "WBT",
        "FIGR_HELOC",
        "LEO",
        "RAIN",
        "USDE",
        "USDS",
        "SUSDS",
    }
)
_DESK_BANNER_NAME_FRAGMENTS = (
    "wrapped",
    "bridged",
    "staked",
    "restaked",
    "heloc",
)


@dataclass(frozen=True, slots=True)
class CoinGeckoMarket:
    """Ein Eintrag aus dem Market-Cap-Ranking."""

    id: str
    symbol: str
    name: str
    market_cap: float | None
    market_cap_rank: int


@dataclass(frozen=True, slots=True)
class CoinGeckoLiveMarket:
    """Top-Coin mit Live-Preis, 24h-Change und 7d-Sparkline (Desk-Banner)."""

    id: str
    symbol: str
    name: str
    market_cap_rank: int
    price_usd: float
    change_24h_pct: float | None
    market_cap_usd: float | None
    volume_24h_usd: float | None
    circulating_supply: float | None
    image_url: str | None
    sparkline: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class CoinGeckoTicker:
    """Ein Boersen-Ticker aus ``/coins/{id}/tickers``."""

    base: str
    target: str
    market_name: str
    market_identifier: str


class CoinGeckoClient:
    """Oeffentliche CoinGecko-REST-API fuer Market-Cap-Rankings."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_client = client is None
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
        self._rate_limiter = RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD_SECONDS)

    async def fetch_top_markets(self, limit: int = 1000) -> list[CoinGeckoMarket]:
        """Top-``limit`` Coins nach Market Cap (USD) laden."""
        if limit <= 0:
            return []

        collected: list[CoinGeckoMarket] = []
        page = 1
        while len(collected) < limit:
            remaining = limit - len(collected)
            per_page = min(MAX_PER_PAGE, remaining)
            batch = await self._fetch_markets_page(page=page, per_page=per_page)
            if not batch:
                break
            collected.extend(batch)
            if len(batch) < per_page:
                break
            page += 1

        markets = collected[:limit]
        logger.info("coingecko_top_markets_loaded", requested=limit, received=len(markets))
        return markets

    async def fetch_live_markets(self, limit: int = 10) -> list[CoinGeckoLiveMarket]:
        """Top-N Spot-Majors inkl. Preis, 24h-Change und 7d-Sparkline.

        CoinGecko mischt Wrapped/RWA/Exchange-Tokens in ``market_cap_desc``.
        Wir laden einen groesseren Pool und filtern auf handelbare Majors
        (BTC/ETH/USDT/BNB/…), dann Display-Rank 1..N.
        """
        if limit <= 0:
            return []
        # Extra headroom so denylisted junk does not starve the banner.
        fetch_n = min(MAX_PER_PAGE, max(limit * 5, 40))
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": fetch_n,
            "page": 1,
            "sparkline": "true",
            "price_change_percentage": "24h",
        }
        await self._rate_limiter.acquire()
        response = await request_with_retry(
            self._client,
            "GET",
            "/coins/markets",
            max_retries=self._settings.http_max_retries,
            params=params,
        )
        if response.status_code >= 400:
            raise MarketDataError(
                f"CoinGecko-Fehler HTTP {response.status_code} bei /coins/markets (live).",
                detail=response.text[:200],
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MarketDataError(
                "Antwort von CoinGecko war kein gueltiges JSON.",
                detail=response.text[:200],
            ) from exc
        if not isinstance(payload, list):
            raise MarketDataError(
                "Unerwartete Antwort von CoinGecko /coins/markets (live).",
                detail=str(payload)[:200],
            )

        markets: list[CoinGeckoLiveMarket] = []
        for item in payload:
            parsed = _parse_live_market(item)
            if parsed is None or not _is_desk_banner_coin(parsed):
                continue
            markets.append(parsed)
            if len(markets) >= limit:
                break

        # Stable display ranks 1..N (CoinGecko rank may skip after filtering).
        ranked = [
            CoinGeckoLiveMarket(
                id=m.id,
                symbol=m.symbol,
                name=m.name,
                market_cap_rank=index,
                price_usd=m.price_usd,
                change_24h_pct=m.change_24h_pct,
                market_cap_usd=m.market_cap_usd,
                volume_24h_usd=m.volume_24h_usd,
                circulating_supply=m.circulating_supply,
                image_url=m.image_url,
                sparkline=m.sparkline,
            )
            for index, m in enumerate(markets[:limit], start=1)
        ]
        logger.info(
            "coingecko_live_markets_loaded",
            requested=limit,
            fetched=fetch_n,
            received=len(ranked),
        )
        return ranked

    async def fetch_coin_tickers(self, coin_id: str, *, max_pages: int = 2) -> list[CoinGeckoTicker]:
        """Boersen-Ticker eines Coins laden — fuer das Mapping auf CEX-Symbole."""
        normalized_id = coin_id.strip()
        if not normalized_id:
            return []

        collected: list[CoinGeckoTicker] = []
        for page in range(1, max_pages + 1):
            batch = await self._fetch_tickers_page(normalized_id, page=page)
            if not batch:
                break
            collected.extend(batch)
            if len(batch) < 100:
                break
        return collected

    async def health_check(self) -> bool:
        try:
            await self._rate_limiter.acquire()
            response = await request_with_retry(
                self._client,
                "GET",
                "/ping",
                max_retries=self._settings.http_max_retries,
            )
            return response.status_code < 400
        except Exception as exc:
            logger.warning("coingecko_health_check_failed", error=str(exc))
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _fetch_markets_page(self, *, page: int, per_page: int) -> list[CoinGeckoMarket]:
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": page,
            "sparkline": "false",
        }
        await self._rate_limiter.acquire()
        response = await request_with_retry(
            self._client,
            "GET",
            "/coins/markets",
            max_retries=self._settings.http_max_retries,
            params=params,
        )
        if response.status_code >= 400:
            raise MarketDataError(
                f"CoinGecko-Fehler HTTP {response.status_code} bei /coins/markets.",
                detail=response.text[:200],
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise MarketDataError(
                "Antwort von CoinGecko war kein gueltiges JSON.",
                detail=response.text[:200],
            ) from exc

        if not isinstance(payload, list):
            raise MarketDataError(
                "Unerwartete Antwort von CoinGecko /coins/markets.",
                detail=str(payload)[:200],
            )

        markets: list[CoinGeckoMarket] = []
        for item in payload:
            parsed = _parse_market(item)
            if parsed is not None:
                markets.append(parsed)
        return markets

    async def _fetch_tickers_page(self, coin_id: str, *, page: int) -> list[CoinGeckoTicker]:
        params = {
            "include_exchange_logo": "false",
            "page": page,
            "order": "trust_score_desc",
        }
        await self._rate_limiter.acquire()
        response = await request_with_retry(
            self._client,
            "GET",
            f"/coins/{coin_id}/tickers",
            max_retries=self._settings.http_max_retries,
            params=params,
        )
        if response.status_code >= 400:
            raise MarketDataError(
                f"CoinGecko-Fehler HTTP {response.status_code} bei /coins/{coin_id}/tickers.",
                detail=response.text[:200],
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise MarketDataError(
                "Antwort von CoinGecko war kein gueltiges JSON.",
                detail=response.text[:200],
            ) from exc

        if not isinstance(payload, dict):
            raise MarketDataError(
                f"Unerwartete Antwort von CoinGecko /coins/{coin_id}/tickers.",
                detail=str(payload)[:200],
            )

        tickers_raw = payload.get("tickers")
        if not isinstance(tickers_raw, list):
            return []

        tickers: list[CoinGeckoTicker] = []
        for item in tickers_raw:
            parsed = _parse_ticker(item)
            if parsed is not None:
                tickers.append(parsed)
        return tickers


def exchange_matches_provider(ticker: CoinGeckoTicker, provider_name: str) -> bool:
    """Pruefen, ob ein CoinGecko-Ticker zu unserem Provider passt."""
    aliases = EXCHANGE_IDENTIFIER_ALIASES.get(provider_name.lower(), frozenset({provider_name.lower()}))
    identifier = ticker.market_identifier.lower().strip()
    name = ticker.market_name.lower().strip()
    return identifier in aliases or name in aliases or provider_name.lower() in name


def _parse_market(item: Any) -> CoinGeckoMarket | None:
    if not isinstance(item, dict):
        return None
    coin_id = str(item.get("id") or "").strip()
    symbol = str(item.get("symbol") or "").strip().upper()
    if not coin_id or not symbol:
        return None
    rank_raw = item.get("market_cap_rank")
    if rank_raw is None:
        return None
    try:
        rank = int(rank_raw)
    except (TypeError, ValueError):
        return None

    market_cap_raw = item.get("market_cap")
    market_cap: float | None
    try:
        market_cap = float(market_cap_raw) if market_cap_raw is not None else None
    except (TypeError, ValueError):
        market_cap = None

    return CoinGeckoMarket(
        id=coin_id,
        symbol=symbol,
        name=str(item.get("name") or symbol),
        market_cap=market_cap,
        market_cap_rank=rank,
    )


def _is_desk_banner_coin(market: CoinGeckoLiveMarket) -> bool:
    """True for spot majors; false for wrapped/bridged/RWA/exchange oddities."""
    if market.id in _DESK_BANNER_EXCLUDED_IDS:
        return False
    if market.symbol.upper() in _DESK_BANNER_EXCLUDED_SYMBOLS:
        return False
    name_l = market.name.lower()
    if any(fragment in name_l for fragment in _DESK_BANNER_NAME_FRAGMENTS):
        return False
    return True


def _parse_live_market(item: Any) -> CoinGeckoLiveMarket | None:
    if not isinstance(item, dict):
        return None
    coin_id = str(item.get("id") or "").strip()
    symbol = str(item.get("symbol") or "").strip().upper()
    if not coin_id or not symbol:
        return None
    rank_raw = item.get("market_cap_rank")
    price_raw = item.get("current_price")
    if rank_raw is None or price_raw is None:
        return None
    try:
        rank = int(rank_raw)
        price = float(price_raw)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    change_raw = item.get("price_change_percentage_24h")
    if change_raw is None:
        change_raw = item.get("price_change_percentage_24h_in_currency")
    try:
        change = float(change_raw) if change_raw is not None else None
    except (TypeError, ValueError):
        change = None

    def _opt_float(key: str) -> float | None:
        raw = item.get(key)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    spark_raw = item.get("sparkline_in_7d")
    spark_prices: list[float] = []
    if isinstance(spark_raw, dict):
        series = spark_raw.get("price")
        if isinstance(series, list):
            for point in series:
                try:
                    spark_prices.append(float(point))
                except (TypeError, ValueError):
                    continue

    image = item.get("image")
    image_url = str(image).strip() if image else None
    if image_url == "":
        image_url = None

    return CoinGeckoLiveMarket(
        id=coin_id,
        symbol=symbol,
        name=str(item.get("name") or symbol),
        market_cap_rank=rank,
        price_usd=price,
        change_24h_pct=change,
        market_cap_usd=_opt_float("market_cap"),
        volume_24h_usd=_opt_float("total_volume"),
        circulating_supply=_opt_float("circulating_supply"),
        image_url=image_url,
        sparkline=tuple(spark_prices),
    )


def _parse_ticker(item: Any) -> CoinGeckoTicker | None:
    if not isinstance(item, dict):
        return None
    base = str(item.get("base") or "").strip().upper()
    target = str(item.get("target") or "").strip().upper()
    market = item.get("market")
    if not base or not target or not isinstance(market, dict):
        return None
    identifier = str(market.get("identifier") or "").strip().lower()
    name = str(market.get("name") or "").strip()
    if not identifier and not name:
        return None
    return CoinGeckoTicker(
        base=base,
        target=target,
        market_name=name,
        market_identifier=identifier,
    )
