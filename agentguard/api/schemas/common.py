"""Reusable public API response contracts."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ValidationIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    location: tuple[str, ...]
    message: str
    type: str


class ProblemDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: str
    errors: tuple[ValidationIssue, ...] | None = None
