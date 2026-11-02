"""Centralized problem-details error handling for the API."""
from __future__ import annotations

import logging
from http import HTTPStatus
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentguard.api.schemas.common import ProblemDetail, ValidationIssue

LOGGER = logging.getLogger("agentguard.api.errors")

_HTTP_CODES = {
    400: "invalid_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "request_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    503: "service_unavailable",
}


class ApiError(Exception):
    """Controlled application error that can be exposed through the API."""

    def __init__(
        self,
        status_code: int,
        code: str,
        title: str,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.title = title
        self.detail = detail


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else uuid4().hex


def _http_title(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "HTTP error"


def _problem(
    request: Request,
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    errors: tuple[ValidationIssue, ...] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    payload = ProblemDetail(
        type=f"https://agentguard.local/problems/{code.replace('_', '-')}",
        title=title,
        status=status_code,
        detail=detail,
        instance=request.url.path,
        code=code,
        request_id=request_id,
        errors=errors,
    ).model_dump(mode="json", exclude_none=True)
    response_headers = {"X-Request-ID": request_id}
    if headers:
        response_headers.update(headers)
    response_headers["X-Content-Type-Options"] = "nosniff"
    return JSONResponse(
        status_code=status_code,
        content=payload,
        media_type="application/problem+json",
        headers=response_headers,
    )


def _validation_issues(exc: RequestValidationError) -> tuple[ValidationIssue, ...]:
    return tuple(
        ValidationIssue(
            location=tuple(str(part) for part in error.get("loc", ())),
            message=str(error.get("msg", "Invalid value")),
            type=str(error.get("type", "value_error")),
        )
        for error in exc.errors()
    )


def _is_malformed_json(exc: RequestValidationError) -> bool:
    return any(error.get("type") == "json_invalid" for error in exc.errors())


async def _request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    if _is_malformed_json(exc):
        return _problem(
            request,
            status_code=400,
            code="invalid_request",
            title="Invalid request",
            detail="The request body is not valid JSON.",
        )
    return _problem(
        request,
        status_code=422,
        code="validation_error",
        title="Request validation failed",
        detail="One or more request fields are invalid.",
        errors=_validation_issues(exc),
    )


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    status_code = exc.status_code
    detail = (
        "The request could not be completed."
        if status_code >= 500
        else str(exc.detail)
        if isinstance(exc.detail, str)
        else "Request failed."
    )
    return _problem(
        request,
        status_code=status_code,
        code=_HTTP_CODES.get(status_code, "http_error"),
        title=_http_title(status_code),
        detail=detail,
        headers=exc.headers,
    )


async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    if exc.status_code >= 500:
        LOGGER.exception(
            "API application error",
            extra={"request_id": _request_id(request), "path": request.url.path},
        )
    return _problem(
        request,
        status_code=exc.status_code,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    LOGGER.exception(
        "Unhandled API exception",
        extra={
            "request_id": _request_id(request),
            "method": request.method,
            "path": request.url.path,
        },
    )
    return _problem(
        request,
        status_code=500,
        code="internal_error",
        title="Internal server error",
        detail="An unexpected error occurred. Use the request ID to correlate server logs.",
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _request_validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
