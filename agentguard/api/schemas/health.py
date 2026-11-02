"""Health response schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthChecks(BaseModel):
    model_config = ConfigDict(frozen=True)

    application: Literal["ok"] = "ok"


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str
    checks: HealthChecks = Field(default_factory=HealthChecks)
