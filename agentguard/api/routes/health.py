"""Application liveness route."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from agentguard.api.config import ApiSettings
from agentguard.api.dependencies import get_settings
from agentguard.api.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Application liveness",
    description=(
        "Reports that the API process is running. This endpoint does not check "
        "PostgreSQL, an LLM provider, or any other external dependency."
    ),
)
def health(settings: ApiSettings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        service=settings.service_name,
        version=settings.version,
        environment=settings.environment,
    )
