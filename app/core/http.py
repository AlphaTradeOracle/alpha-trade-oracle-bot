"""Gemeinsame HTTP-Bausteine: Retry mit Backoff und ein Token-Bucket-Rate-Limiter."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

import httpx

from app.core.errors import MarketDataError, RateLimitError
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Statuscodes, bei denen ein erneuter Versuch sinnvoll ist.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class RateLimiter:
    """Asynchroner Token-Bucket.

    Binance rechnet in Gewichtseinheiten pro Minute. Der Limiter haelt die
    Aufrufrate konservativ darunter, damit HTTP 429/418 gar nicht erst auftritt.
    """

    def __init__(self, max_calls: int, period_seconds: float) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls muss positiv sein")
        self._max_calls = max_calls
        self._period = period_seconds
        self._tokens = float(max_calls)
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, weight: int = 1) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._updated_at = now
                self._tokens = min(
                    float(self._max_calls),
                    self._tokens + elapsed * (self._max_calls / self._period),
                )
                if self._tokens >= weight:
                    self._tokens -= weight
                    return
                deficit = weight - self._tokens
                wait_for = deficit * (self._period / self._max_calls)
            await asyncio.sleep(min(wait_for, self._period))


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    **kwargs: object,
) -> httpx.Response:
    """HTTP-Aufruf mit Exponential Backoff, Jitter und Rate-Limit-Behandlung.

    Nicht-retrybare Fehler (z. B. 400, 404) werden sofort weitergegeben, damit
    ein falsches Symbol nicht drei Wartezyklen kostet.
    """
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            response = await client.request(method, url, **kwargs)  # type: ignore[arg-type]
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            delay = min(max_delay, base_delay * 2**attempt) + random.uniform(0, 0.3)
            logger.warning(
                "http_transport_retry",
                url=url,
                attempt=attempt + 1,
                max_retries=max_retries,
                delay_seconds=round(delay, 2),
                error=str(exc),
            )
            await asyncio.sleep(delay)
            continue

        if response.status_code in RETRYABLE_STATUS:
            retry_after = _retry_after_seconds(response)
            if response.status_code in (429, 418) and attempt == max_retries:
                raise RateLimitError(
                    f"Rate Limit erreicht (HTTP {response.status_code}) bei {url}.",
                    retry_after_seconds=retry_after,
                )
            if attempt == max_retries:
                break
            delay = (
                retry_after
                if retry_after is not None
                else min(max_delay, base_delay * 2**attempt) + random.uniform(0, 0.3)
            )
            logger.warning(
                "http_status_retry",
                url=url,
                status_code=response.status_code,
                attempt=attempt + 1,
                delay_seconds=round(delay, 2),
            )
            await asyncio.sleep(delay)
            continue

        return response

    if last_error is not None:
        raise MarketDataError(
            f"HTTP-Aufruf an {url} ist nach {max_retries + 1} Versuchen fehlgeschlagen.",
            detail=str(last_error),
        ) from last_error
    raise MarketDataError(f"HTTP-Aufruf an {url} war nach mehreren Versuchen nicht erfolgreich.")


async def run_with_semaphore[T](
    semaphore: asyncio.Semaphore, func: Callable[[], Awaitable[T]]
) -> T:
    """Nebenlaeufigkeit begrenzen, ohne den Aufrufer mit Locking zu belasten."""
    async with semaphore:
        return await func()
