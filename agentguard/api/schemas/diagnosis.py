"""API request and response DTOs for diagnosis and recommendations."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentguard.diagnostics.types import (
    FailureCategory,
    PatternSeverity,
    PatternType,
    RecoveryAction,
    RecoveryRisk,
)
from agentguard.schemas.events import ExecutionEvent


class DiagnosisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[ExecutionEvent] = Field(max_length=1000)


class EvidenceResponse(BaseModel):
    description: str
    source: str
    event_ids: tuple[str, ...]
    observed_value: float | None
    threshold: float | None


class FailurePatternResponse(BaseModel):
    pattern_type: PatternType
    severity: PatternSeverity
    description: str
    event_ids: tuple[str, ...]
    tool_name: str | None
    count: int
    threshold: int | None
    metadata: dict[str, Any]


class MLRiskResponse(BaseModel):
    probability: float
    threshold: float
    model_name: str | None
    source: str
    predicted_failure: bool


class FailureDiagnosisResponse(BaseModel):
    is_failure: bool
    primary_category: FailureCategory
    secondary_categories: tuple[FailureCategory, ...]
    confidence: float
    summary: str
    execution_id: str | None
    task_id: str | None
    tool_name: str | None
    terminal_observed: bool
    execution_status: str | None
    partial_results_available: bool
    patterns: tuple[FailurePatternResponse, ...]
    evidence: tuple[EvidenceResponse, ...]
    ml_risk: MLRiskResponse | None


class RecoveryRecommendationResponse(BaseModel):
    action: RecoveryAction
    diagnosis_category: FailureCategory
    target_tool: str | None
    risk: RecoveryRisk
    requires_human_approval: bool
    max_attempts: int
    rationale: str
    expected_benefit: str
    evidence_event_ids: tuple[str, ...]


class DiagnosisResponse(BaseModel):
    diagnosis: FailureDiagnosisResponse
    recommendations: tuple[RecoveryRecommendationResponse, ...]
    ml_error: str | None = None
