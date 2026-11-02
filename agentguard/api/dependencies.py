"""Typed dependency providers shared by API routes."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from agentguard.api.config import ApiSettings
from agentguard.application.execution_service import ExecutionService
from agentguard.application.trace_service import TraceService


@dataclass(frozen=True)
class AppServices:
    settings: ApiSettings
    execution_service: ExecutionService
    trace_service: TraceService


def get_services(request: Request) -> AppServices:
    services = getattr(request.app.state, "services", None)
    if not isinstance(services, AppServices):
        raise RuntimeError("Application services were not initialized")
    return services


def get_settings(services: AppServices = Depends(get_services)) -> ApiSettings:
    return services.settings


def get_execution_service(
    services: AppServices = Depends(get_services),
) -> ExecutionService:
    return services.execution_service


def get_trace_service(services: AppServices = Depends(get_services)) -> TraceService:
    return services.trace_service
