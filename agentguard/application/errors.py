"""Domain errors that map cleanly to API problem-details responses."""
from __future__ import annotations


class ApplicationError(Exception):
    """Base class for errors safely exposed by the application boundary."""

    status_code = 500
    code = "application_error"
    title = "Application error"
    detail = "The request could not be completed."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or type(self).detail
        super().__init__(self.detail)


class ExecutionNotFoundError(ApplicationError):
    status_code = 404
    code = "execution_not_found"
    title = "Execution not found"
    detail = "No persisted execution exists for the supplied identifier."


class PersistenceNotConfiguredError(ApplicationError):
    status_code = 503
    code = "persistence_not_configured"
    title = "Persistence not configured"
    detail = "Execution persistence is not configured for this deployment."


class PersistenceUnavailableError(ApplicationError):
    status_code = 503
    code = "persistence_unavailable"
    title = "Persistence unavailable"
    detail = "The execution store is temporarily unavailable."


class LLMNotConfiguredError(ApplicationError):
    status_code = 503
    code = "llm_not_configured"
    title = "LLM provider not configured"
    detail = "LLM mode requires a configured provider and model."


class ExecutionUnavailableError(ApplicationError):
    status_code = 503
    code = "execution_unavailable"
    title = "Execution unavailable"
    detail = "The agent execution service is currently unavailable."


class InvalidDiagnosisPrefixError(ApplicationError):
    status_code = 422
    code = "invalid_diagnosis_prefix"
    title = "Invalid diagnosis prefix"
    detail = "The supplied event prefix does not satisfy the diagnosis trace contract."


class DiagnosisTraceTooLargeError(ApplicationError):
    status_code = 413
    code = "diagnosis_trace_too_large"
    title = "Diagnosis trace too large"
    detail = "The persisted execution has too many events for one diagnosis request."
