"""Typed dependency providers shared by API routes."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from agentguard.api.config import ApiSettings


@dataclass(frozen=True)
class AppServices:
    settings: ApiSettings


def get_services(request: Request) -> AppServices:
    services = getattr(request.app.state, "services", None)
    if not isinstance(services, AppServices):
        raise RuntimeError("Application services were not initialized")
    return services


def get_settings(services: AppServices = Depends(get_services)) -> ApiSettings:
    return services.settings
