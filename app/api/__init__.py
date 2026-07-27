"""HTTP-Schicht. Enthaelt keine Fachlogik, nur Uebersetzung von HTTP zu Services."""

from fastapi import APIRouter

from app.api import health
from app.api.v1 import router as v1_router

router = APIRouter()
router.include_router(health.router)
router.include_router(v1_router)

__all__ = ["router"]
