"""Diagnosis-only endpoints; recommendations are informational, never executed."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from agentguard.api.dependencies import get_diagnosis_service
from agentguard.api.schemas.diagnosis import DiagnosisRequest, DiagnosisResponse
from agentguard.application.diagnosis_service import DiagnosisService

router = APIRouter(tags=["diagnoses"])


@router.post(
    "/diagnoses",
    response_model=DiagnosisResponse,
    status_code=status.HTTP_200_OK,
    summary="Diagnose an observed event prefix",
    description=(
        "Returns a deterministic diagnosis and informational recovery recommendations. "
        "No recovery action is executed and no diagnosis is persisted."
    ),
)
def diagnose_events(
    request: DiagnosisRequest,
    service: DiagnosisService = Depends(get_diagnosis_service),
) -> DiagnosisResponse:
    return service.diagnose_events(request.events)


@router.post(
    "/executions/{execution_id}/diagnoses",
    response_model=DiagnosisResponse,
    status_code=status.HTTP_200_OK,
    summary="Diagnose a persisted execution",
    description=(
        "Loads a bounded ordered page of persisted execution events and returns "
        "diagnosis and recommendations without modifying the execution."
    ),
)
def diagnose_execution(
    execution_id: UUID,
    service: DiagnosisService = Depends(get_diagnosis_service),
) -> DiagnosisResponse:
    return service.diagnose_execution(str(execution_id))
