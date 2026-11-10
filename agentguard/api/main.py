"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from agentguard.api.config import ApiSettings
from agentguard.api.dependencies import AppServices
from agentguard.api.errors import install_exception_handlers
from agentguard.api.middleware import RequestIdMiddleware
from agentguard.api.router import API_V1_PREFIX, build_api_router
from agentguard.application.diagnosis_service import DiagnosisService
from agentguard.application.execution_service import (
    AgentExecutionRunner,
    DatabaseTraceWriter,
    ExecutionService,
)
from agentguard.application.trace_service import DatabaseTraceReader, TraceService
from agentguard.config.database import DatabaseSettings
from agentguard.config.settings import load_settings
from agentguard.database.connection import create_session_factory
from agentguard.diagnostics.service import DiagnosticService, MLRiskScorer


def create_app(
    settings: ApiSettings | None = None,
    *,
    services: AppServices | None = None,
    ml_scorer: MLRiskScorer | None = None,
) -> FastAPI:
    api_settings = settings or ApiSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if services is not None:
            app.state.services = services
            yield
            return

        database_settings = DatabaseSettings.from_env()
        agent_settings = load_settings()
        session_factory = (
            create_session_factory(database_settings)
            if database_settings.enabled
            else None
        )
        trace_reader = DatabaseTraceReader(session_factory)
        try:
            app.state.services = AppServices(
                settings=api_settings,
                execution_service=ExecutionService(
                    AgentExecutionRunner(agent_settings),
                    persistence_enabled=database_settings.enabled,
                    llm_configured=agent_settings.llm_configured,
                    trace_writer=(
                        DatabaseTraceWriter(
                            session_factory,
                            store_task_text=database_settings.store_task_text,
                        )
                        if session_factory is not None
                        else None
                    ),
                ),
                trace_service=TraceService(
                    trace_reader,
                    persistence_enabled=database_settings.enabled,
                ),
                diagnosis_service=DiagnosisService(
                    DiagnosticService(ml_scorer=ml_scorer),
                    trace_reader,
                    persistence_enabled=database_settings.enabled,
                ),
            )
            yield
        finally:
            if session_factory is not None:
                session_factory.kw["bind"].dispose()

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
