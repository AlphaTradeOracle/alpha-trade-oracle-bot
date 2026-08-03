"""Public leverage/perp coverage across major venues.

Used by universe refresh to keep only bases that have a perpetual/futures
market somewhere (Binance USD-M, KuCoin Futures, Aster, Hyperliquid).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.http import RateLimiter, request_with_retry
from app.core.logging import get_logger

logger = get_logger(__name__)

RATE_LIMIT_CALLS = 20
RATE_LIMIT_PERIOD_SECONDS = 60.0

VENUE_LOADERS = ("binance", "kucoin", "aster", "hyperliquid")

#: Optional injectable loaders for unit tests: venue -> async () -> set[str]
LeverageLoader = Callable[[], Awaitable[set[str]]]


def normalize_base(base: str) -> str:
    b = base.upper().strip()
    if b == "XBT":
        return "BTC"
    return b


def base_alias_candidates(base: str) -> list[str]:
    """Desk base plus common perp aliases (1000x / k-prefix / unwrap)."""
    b = normalize_base(base)
    if not b:
        return []
    out: list[str] = [b]
    for candidate in (f"1000{b}", f"K{b}"):
        if candidate not in out:
            out.append(candidate)
    if b.startswith("1000") and len(b) > 4:
        bare = b[4:]
        if bare and bare not in out:
            out.append(bare)
    if b.startswith("K") and len(b) > 2:
        bare = b[1:]
        if bare and bare not in out:
            out.append(bare)
    if b == "BTC" and "XBT" not in out:
        out.append("XBT")
    if b == "XBT" and "BTC" not in out:
        out.append("BTC")
    return out


def base_has_leverage(base: str, tradable: set[str]) -> bool:
    """True if ``base`` (or a common alias/k-prefix) is in the tradable set."""
    b = normalize_base(base)
    if not b or not tradable:
        return False
    if b in tradable:
        return True
    aliases = {
        "BTC": {"XBT", "BTC"},
        "WBTC": {"BTC", "WBTC"},
        "WETH": {"ETH", "WETH"},
    }
    for canon, alts in aliases.items():
        if b in alts and (canon in tradable or bool(alts & tradable)):
            return True
    # Hyperliquid 1000PEPE / kPEPE style
    if f"1000{b}" in tradable or b.startswith("1000") and b[4:] in tradable:
        return True
    if f"K{b}" in tradable:
        return True
    for vb in tradable:
        if vb.startswith("K") and vb[1:] == b:
            return True
    return False


class LeverageCoverageClient:
    """Fetches tradable perp bases from configured venues (no auth)."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        loaders: dict[str, LeverageLoader] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._settings.http_timeout_seconds),
            headers={"User-Agent": "alpha-trade-oracle-bot/leverage-coverage", "Accept": "application/json"},
        )
        self._rate_limiter = RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD_SECONDS)
        self._loaders = loaders

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _enabled_venues(self) -> list[str]:
        raw = getattr(self._settings, "universe_leverage_venues", "") or ""
        names = [part.strip().lower() for part in raw.split(",") if part.strip()]
        if not names:
            return list(VENUE_LOADERS)
        return [n for n in names if n in VENUE_LOADERS]

    async def fetch_tradable_bases(self) -> set[str]:
        """Union of perp bases across enabled venues."""
        if self._loaders is not None:
            out: set[str] = set()
            for name, loader in self._loaders.items():
                try:
                    bases = await loader()
                    out |= {normalize_base(b) for b in bases}
                    logger.info("leverage_venue_loaded", venue=name, bases=len(bases))
                except Exception as exc:
                    logger.warning("leverage_venue_failed", venue=name, error=str(exc))
            return out

        out = set()
        for venue in self._enabled_venues():
            try:
                bases = await self._load_venue(venue)
                out |= bases
                logger.info("leverage_venue_loaded", venue=venue, bases=len(bases))
            except Exception as exc:
                logger.warning("leverage_venue_failed", venue=venue, error=str(exc))
        logger.info("leverage_coverage_ready", venues=self._enabled_venues(), bases=len(out))
        return out

    async def _load_venue(self, venue: str) -> set[str]:
        if venue == "binance":
            return await self._binance()
        if venue == "kucoin":
            return await self._kucoin()
        if venue == "aster":
            return await self._aster()
        if venue == "hyperliquid":
            return await self._hyperliquid()
        return set()

    async def _get_json(self, url: str) -> Any:
        await self._rate_limiter.acquire()
        response = await request_with_retry(self._client, "GET", url)
        return response.json()

    async def _post_json(self, url: str, body: dict[str, Any]) -> Any:
        await self._rate_limiter.acquire()
        response = await request_with_retry(self._client, "POST", url, json=body)
        return response.json()

    async def _binance(self) -> set[str]:
        data = await self._get_json("https://fapi.binance.com/fapi/v1/exchangeInfo")
        bases: set[str] = set()
        for item in data.get("symbols") or []:
            if item.get("contractType") != "PERPETUAL":
                continue
            if item.get("status") != "TRADING":
                continue
            if item.get("quoteAsset") not in {"USDT", "USDC"}:
                continue
            bases.add(normalize_base(str(item.get("baseAsset") or "")))
        return {b for b in bases if b}

    async def _kucoin(self) -> set[str]:
        data = await self._get_json("https://api-futures.kucoin.com/api/v1/contracts/active")
        bases: set[str] = set()
        for item in data.get("data") or []:
            base = normalize_base(str(item.get("baseCurrency") or ""))
            if base:
                bases.add(base)
        return bases

    async def _aster(self) -> set[str]:
        data = await self._get_json("https://fapi.asterdex.com/fapi/v1/exchangeInfo")
        bases: set[str] = set()
        for item in data.get("symbols") or []:
            if item.get("status") and item.get("status") != "TRADING":
                continue
            if item.get("contractType") and item.get("contractType") != "PERPETUAL":
                continue
            base = normalize_base(str(item.get("baseAsset") or ""))
            if not base:
                sym = str(item.get("symbol") or "").upper()
                for quote in ("USDT", "USDC"):
                    if sym.endswith(quote) and len(sym) > len(quote):
                        base = normalize_base(sym[: -len(quote)])
                        break
            if base:
                bases.add(base)
        return bases

    async def _hyperliquid(self) -> set[str]:
        data = await self._post_json("https://api.hyperliquid.xyz/info", {"type": "meta"})
        bases: set[str] = set()
        for item in data.get("universe") or []:
            if item.get("isDelisted"):
                continue
            name = str(item.get("name") or "").upper()
            if not name:
                continue
            bases.add(normalize_base(name))
            if name.startswith("K") and len(name) > 2:
                bases.add(normalize_base(name[1:]))
        return bases


def parse_leverage_venues(raw: str | Iterable[str]) -> list[str]:
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    else:
        parts = [str(p).strip().lower() for p in raw if str(p).strip()]
    return [p for p in parts if p in VENUE_LOADERS]
