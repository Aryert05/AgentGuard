"""Versioned API router composition."""
from __future__ import annotations

from fastapi import APIRouter

from agentguard.api.routes import health

API_V1_PREFIX = "/api/v1"


def build_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(health.router)
    return router
