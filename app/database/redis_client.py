"""Redis-Anbindung. Ein Ausfall degradiert das System, stoppt es aber nicht."""

from __future__ import annotations

import redis.asyncio as redis

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: redis.Redis | None = None


def get_redis(settings: Settings | None = None) -> redis.Redis:
    global _client
    if _client is None:
        cfg = settings or get_settings()
        _client = redis.from_url(
            cfg.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            health_check_interval=30,
        )
        logger.info("redis_client_created")
    return _client


async def check_redis_connection() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception as exc:
        logger.warning("redis_check_failed", error=str(exc))
        return False


async def close_redis() -> None:
    global _client
    if _client is not None:
        # redis-py 5 stellt ``aclose`` bereit; aeltere Stubs kennen nur ``close``.
        if hasattr(_client, "aclose"):
            await _client.aclose()  # type: ignore[attr-defined]
        else:
            await _client.close()  # type: ignore[misc]
        logger.info("redis_client_closed")
    _client = None
