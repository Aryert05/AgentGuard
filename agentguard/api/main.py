"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from agentguard.api.config import ApiSettings
from agentguard.api.dependencies import AppServices
from agentguard.api.errors import install_exception_handlers
from agentguard.api.middleware import RequestIdMiddleware
from agentguard.api.router import API_V1_PREFIX, build_api_router


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    api_settings = settings or ApiSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = AppServices(settings=api_settings)
        yield

    app = FastAPI(
        title=api_settings.service_name,
        description=api_settings.description,
        version=api_settings.version,
        lifespan=lifespan,
        docs_url="/docs" if api_settings.docs_enabled else None,
        redoc_url="/redoc" if api_settings.docs_enabled else None,
        openapi_url="/openapi.json" if api_settings.docs_enabled else None,
        openapi_tags=[
            {
                "name": "health",
                "description": "Application liveness and future readiness checks.",
            }
        ],
    )
    app.add_middleware(RequestIdMiddleware)
    install_exception_handlers(app)
    app.include_router(build_api_router(), prefix=API_V1_PREFIX)
    return app
