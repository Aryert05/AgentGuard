"""Application orchestration for stateless and persisted-trace diagnosis."""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from pydantic import ValidationError

from agentguard.application.errors import (
    DiagnosisTraceTooLargeError,
    ExecutionNotFoundError,
    InvalidDiagnosisPrefixError,
    PersistenceNotConfiguredError,
    PersistenceUnavailableError,
)
from agentguard.application.protocols import TraceReader
from agentguard.diagnostics.service import DiagnosticService as CoreDiagnosticService
from agentguard.diagnostics.types import DiagnosticValidationError, DiagnosisReport
from agentguard.schemas.events import ExecutionEvent
from agentguard.telemetry.privacy import redact_metadata, redact_text

if TYPE_CHECKING:
    from agentguard.api.schemas.diagnosis import DiagnosisResponse

MAX_DIAGNOSIS_EVENTS = 1000
_SAFE_ML_ERROR = "Optional ML scoring failed; rule-based diagnosis is returned."


class DiagnosisService:
    """Map Phase 3.4 diagnosis results to API DTOs without executing recovery."""

    def __init__(
        self,
        diagnostics: CoreDiagnosticService,
        trace_reader: TraceReader,
        *,
        persistence_enabled: bool,
    ) -> None:
        self.diagnostics = diagnostics
        self.trace_reader = trace_reader
        self.persistence_enabled = persistence_enabled

    def diagnose_events(self, events: Sequence[ExecutionEvent]) -> DiagnosisResponse:
        try:
            report = self.diagnostics.analyze(events)
        except DiagnosticValidationError:
            raise InvalidDiagnosisPrefixError() from None
        return _response(report)

    def diagnose_execution(self, execution_id: str) -> DiagnosisResponse:
        if not self.persistence_enabled:
            raise PersistenceUnavailableError()
        try:
            page = self.trace_reader.list_events(
                execution_id,
                limit=MAX_DIAGNOSIS_EVENTS,
                after_event_index=None,
            )
        except PersistenceNotConfiguredError:
            raise PersistenceUnavailableError() from None
        except PersistenceUnavailableError:
            raise
        if page is None:
            raise ExecutionNotFoundError()
        if page.get("next_after_event_index") is not None:
            raise DiagnosisTraceTooLargeError()
        try:
            events = tuple(ExecutionEvent.model_validate(item) for item in page["events"])
        except (KeyError, TypeError, ValidationError):
            raise PersistenceUnavailableError() from None
        try:
            report = self.diagnostics.analyze(events)
        except DiagnosticValidationError:
            # A stored trace that violates the same contract enforced on input
            # is a persistence/data-integrity failure, not a client error.
            raise PersistenceUnavailableError() from None
        return _response(report)


def _response(report: DiagnosisReport) -> DiagnosisResponse:
    from agentguard.api.schemas.diagnosis import (
        DiagnosisResponse,
        EvidenceResponse,
        FailureDiagnosisResponse,
        FailurePatternResponse,
        MLRiskResponse,
        RecoveryRecommendationResponse,
    )

    diagnosis = report.diagnosis
    return DiagnosisResponse(
        diagnosis=FailureDiagnosisResponse(
            is_failure=diagnosis.is_failure,
            primary_category=diagnosis.primary_category,
            secondary_categories=diagnosis.secondary_categories,
            confidence=diagnosis.confidence,
            summary=redact_text(diagnosis.summary) or "",
            execution_id=diagnosis.execution_id,
            task_id=diagnosis.task_id,
            tool_name=diagnosis.tool_name,
            terminal_observed=diagnosis.terminal_observed,
            execution_status=diagnosis.execution_status,
            partial_results_available=diagnosis.partial_results_available,
            patterns=tuple(
                FailurePatternResponse.model_validate(
                    {
                        **item.model_dump(),
                        "description": redact_text(item.description) or "",
                        "metadata": redact_metadata(item.metadata),
                    }
                )
                for item in diagnosis.patterns
            ),
            evidence=tuple(
                EvidenceResponse.model_validate(
                    {**item.model_dump(), "description": redact_text(item.description) or ""}
                )
                for item in diagnosis.evidence
            ),
            ml_risk=(
                MLRiskResponse(
                    probability=diagnosis.ml_risk.probability,
                    threshold=diagnosis.ml_risk.threshold,
                    model_name=diagnosis.ml_risk.model_name,
                    source=diagnosis.ml_risk.source,
                    predicted_failure=diagnosis.ml_risk.predicted_failure,
                )
                if diagnosis.ml_risk is not None
                else None
            ),
        ),
        recommendations=tuple(
            RecoveryRecommendationResponse.model_validate(
                {
                    **item.model_dump(),
                    "rationale": redact_text(item.rationale) or "",
                    "expected_benefit": redact_text(item.expected_benefit) or "",
                }
            )
            for item in report.recommendations
        ),
        # Core scorer errors may contain internal exception details. The API
        # reports fallback without exposing those details or a stack trace.
        ml_error=_SAFE_ML_ERROR if report.ml_error else None,
    )
