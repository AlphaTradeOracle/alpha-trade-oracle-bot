"""API-Version 1."""

from fastapi import APIRouter

from app.api.v1 import backtests, signals

router = APIRouter()
router.include_router(signals.router)
router.include_router(backtests.router)

__all__ = ["router"]
