"""Async market-intel feeds (Fear & Greed, Binance funding)."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.http import request_with_retry
from app.core.logging import get_logger

logger = get_logger(__name__)

FNG_URL = "https://api.alternative.me/fng/"
BINANCE_FAPI = "https://fapi.binance.com"


async def fetch_fear_greed(
    *,
    client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Latest Crypto Fear & Greed Index from alternative.me (no key)."""
    cfg = settings or get_settings()
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(cfg.http_timeout_seconds))
    try:
        response = await request_with_retry(
            http, "GET", FNG_URL, params={"limit": 1}, max_retries=2
        )
        if response.status_code >= 400:
            logger.warning("fear_greed_http_error", status=response.status_code)
            return None
        payload = response.json()
        rows = payload.get("data") or []
        if not rows:
            return None
        row = rows[0]
        value = int(float(row["value"]))
        label_raw = str(row.get("value_classification") or "").strip().lower()
        label = label_raw.replace(" ", "_")
        return {
            "value": value,
            "label": label,
            "classification": row.get("value_classification"),
            "timestamp": row.get("timestamp"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("fear_greed_fetch_failed", error=str(exc))
        return None
    finally:
        if own_client:
            await http.aclose()


async def fetch_binance_funding(
    symbol: str,
    *,
    client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
    history_limit: int = 24,
) -> dict[str, Any] | None:
    """Current + recent funding for a USDT-M perpetual (Binance fapi)."""
    cfg = settings or get_settings()
    perp = symbol.upper().strip()
    if not perp.endswith("USDT"):
        perp = f"{perp}USDT"

    own_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(cfg.http_timeout_seconds))
    try:
        premium = await request_with_retry(
            http,
            "GET",
            f"{BINANCE_FAPI}/fapi/v1/premiumIndex",
            params={"symbol": perp},
            max_retries=2,
        )
        if premium.status_code == 400:
            # Symbol not listed as perp — try BTC only later.
            logger.info("funding_symbol_not_listed", symbol=perp)
            return None
        if premium.status_code >= 400:
            logger.warning(
                "funding_premium_http_error", symbol=perp, status=premium.status_code
            )
            return None
        prem = premium.json()
        current = float(prem.get("lastFundingRate") or 0.0)
        mark = float(prem.get("markPrice") or 0.0) if prem.get("markPrice") else None
        next_funding = prem.get("nextFundingTime")

        average = None
        change = None
        history: list[dict[str, Any]] = []
        hist_resp = await request_with_retry(
            http,
            "GET",
            f"{BINANCE_FAPI}/fapi/v1/fundingRate",
            params={"symbol": perp, "limit": history_limit},
            max_retries=2,
        )
        if hist_resp.status_code < 400:
            history = list(hist_resp.json() or [])
            rates = [float(r["fundingRate"]) for r in history if "fundingRate" in r]
            if rates:
                average = sum(rates) / len(rates)
                if len(rates) >= 2:
                    change = rates[-1] - rates[-2]

        return {
            "symbol": perp,
            "current": current,
            "average": average,
            "changeHours": change,
            "markPrice": mark,
            "nextFundingTime": next_funding,
            "historyCount": len(history),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("funding_fetch_failed", symbol=perp, error=str(exc))
        return None
    finally:
        if own_client:
            await http.aclose()


async def fetch_market_feed_bundle(
    *,
    coin_symbol: str | None,
    btc_symbol: str = "BTCUSDT",
    settings: Settings | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Fetch F&G + coin funding + BTC funding in one shared client."""
    cfg = settings or get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.http_timeout_seconds)) as http:
        fear_greed = await fetch_fear_greed(client=http, settings=cfg)
        btc_funding = await fetch_binance_funding(btc_symbol, client=http, settings=cfg)
        coin_funding = None
        if coin_symbol:
            sym = coin_symbol.upper().strip()
            if sym != btc_symbol.upper():
                coin_funding = await fetch_binance_funding(sym, client=http, settings=cfg)
            else:
                coin_funding = btc_funding
        return {
            "fear_greed": fear_greed,
            "btc_funding": btc_funding,
            "coin_funding": coin_funding,
        }
