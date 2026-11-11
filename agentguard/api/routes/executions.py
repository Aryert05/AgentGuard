"""Agent execution submission and persisted trace retrieval routes."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from agentguard.api.dependencies import get_execution_service, get_trace_service
from agentguard.api.schemas.execution import (
    ExecutionEventsResponse,
    ExecutionListResponse,
    ExecutionRequest,
    ExecutionResponse,
    ExecutionStatus,
    ExecutionSummaryResponse,
    OverviewMetricsResponse,
)
from agentguard.application.execution_service import ExecutionService
from agentguard.application.trace_service import TraceService

router = APIRouter(prefix="/executions", tags=["executions"])


@router.post(
    "",
    response_model=ExecutionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit an agent execution",
    description=(
        "Runs the existing AgentGuard agent synchronously. Deterministic mode is "
        "offline-capable; LLM mode requires a configured provider and model. "
        "This endpoint is not idempotent and must not be retried automatically."
    ),
)
def submit_execution(
    request: ExecutionRequest,
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    return service.run(request)


@router.get(
    "/history",
    response_model=ExecutionListResponse,
    summary="List persisted executions",
    description="Returns a paginated list of persisted execution summaries with optional status and search filters.",
)
def list_executions(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: ExecutionStatus | None = Query(default=None),
    query: str | None = Query(default=None),
    service: TraceService = Depends(get_trace_service),
) -> ExecutionListResponse:
    return service.list_executions(
        limit=limit,
        offset=offset,
        status=status,
        query=query,
    )


@router.get(
    "/overview",
    response_model=OverviewMetricsResponse,
    summary="Get execution overview metrics",
    description="Returns aggregate execution statistics and outcomes distribution.",
)
def get_overview_metrics(
    service: TraceService = Depends(get_trace_service),
) -> OverviewMetricsResponse:
    return service.get_overview_metrics()


@router.get(
    "/{execution_id}",
    response_model=ExecutionSummaryResponse,
    summary="Get a persisted execution",
    description=(
        "Returns the stored execution summary. Final output, messages, and raw "
        "tool arguments are not persisted and are never invented."
    ),
)
def get_execution(
    execution_id: UUID,
    service: TraceService = Depends(get_trace_service),
) -> ExecutionSummaryResponse:
    return service.get_execution(str(execution_id))


@router.get(
    "/{execution_id}/events",
    response_model=ExecutionEventsResponse,
    summary="Get persisted execution events",
    description="Returns an ordered, bounded event page using event_index as the cursor.",
)
def list_execution_events(
    execution_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    after_event_index: int | None = Query(default=None, ge=0),
    service: TraceService = Depends(get_trace_service),
) -> ExecutionEventsResponse:
    return service.list_events(
        str(execution_id),
        limit=limit,
        after_event_index=after_event_index,
    )
