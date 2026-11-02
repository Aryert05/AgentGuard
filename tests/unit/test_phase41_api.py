import asyncio
import logging
import re

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agentguard import __version__
from agentguard.api import ApiSettings, create_app
from agentguard.api.errors import ApiError
from agentguard.api.middleware import RequestIdMiddleware
from agentguard.config.settings import ConfigurationError

TEST_SETTINGS = ApiSettings(environment="test", docs_enabled=True)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ValidationPayload(BaseModel):
    count: int


async def _request_id_probe(raw_value: bytes) -> tuple[str, list[tuple[bytes, bytes]]]:
    sent: list[dict] = []

    async def downstream(scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"x-request-id", raw_value), (b"host", b"audit.local")],
        "client": ("127.0.0.1", 1),
        "server": ("audit.local", 80),
        "http_version": "1.1",
        "state": {},
    }
    await RequestIdMiddleware(downstream)(scope, receive, send)
    return scope["state"]["request_id"], sent[0]["headers"]


def test_application_metadata_and_health_response() -> None:
    app = create_app(TEST_SETTINGS)

    assert app.title == "AgentGuard API"
    assert app.version == __version__
    assert app.docs_url == "/docs"
    assert app.openapi_url == "/openapi.json"

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "AgentGuard API",
        "version": __version__,
        "environment": "test",
        "checks": {"application": "ok"},
    }
    assert REQUEST_ID_PATTERN.fullmatch(response.headers["X-Request-ID"])
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_health_is_only_exposed_under_api_v1() -> None:
    app = create_app(TEST_SETTINGS)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "not_found"


def test_openapi_exposes_versioned_health_route() -> None:
    app = create_app(TEST_SETTINGS)

    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/health" in paths
    assert "/health" not in paths


def test_docs_can_be_disabled() -> None:
    app = create_app(ApiSettings(environment="production", docs_enabled=False))

    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_unknown_route_returns_safe_problem_details_and_request_id() -> None:
    app = create_app(TEST_SETTINGS)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/unknown", headers={"X-Request-ID": "interview-probe-1"}
        )

    body = response.json()
    assert response.status_code == 404
    assert body["type"] == "https://agentguard.local/problems/not-found"
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["code"] == "not_found"
    assert body["instance"] == "/api/v1/unknown"
    assert body["request_id"] == "interview-probe-1"
    assert response.headers["X-Request-ID"] == "interview-probe-1"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_missing_request_id_is_generated() -> None:
    app = create_app(TEST_SETTINGS)

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    request_id = response.headers["X-Request-ID"]
    assert REQUEST_ID_PATTERN.fullmatch(request_id)


def test_valid_request_id_is_echoed() -> None:
    app = create_app(TEST_SETTINGS)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/health", headers={"X-Request-ID": "client.Request_1:abc-9"}
        )

    assert response.headers["X-Request-ID"] == "client.Request_1:abc-9"


@pytest.mark.parametrize(
    ("value", "should_echo"),
    [
        ("a" * 128, True),
        ("a" * 129, False),
        ("contains spaces", False),
        ('a"b', False),
    ],
)
def test_client_request_id_validation(value: str, should_echo: bool) -> None:
    app = create_app(TEST_SETTINGS)

    with TestClient(app) as client:
        response = client.get("/api/v1/health", headers={"X-Request-ID": value})

    request_id = response.headers["X-Request-ID"]
    assert REQUEST_ID_PATTERN.fullmatch(request_id)
    assert (request_id == value) is should_echo


@pytest.mark.parametrize(
    ("raw_value", "should_echo"),
    [
        (b"evil\r\nX-Injected: yes", False),
        (b"abc\x00def", False),
        (b"caf\xc3\xa9", False),
        (b"a" * 128, True),
        (b"a" * 129, False),
    ],
)
def test_raw_request_id_header_validation(
    raw_value: bytes, should_echo: bool
) -> None:
    request_id, headers = asyncio.run(_request_id_probe(raw_value))
    decoded_value = raw_value.decode("latin-1")
    header_values = {key.lower(): value for key, value in headers}

    assert REQUEST_ID_PATTERN.fullmatch(request_id)
    assert (request_id == decoded_value) is should_echo
    assert header_values[b"x-request-id"] == request_id.encode("latin-1")
    assert header_values[b"x-content-type-options"] == b"nosniff"
    assert b"x-injected" not in header_values


def test_validation_errors_are_structured_without_input_echo() -> None:
    app = create_app(TEST_SETTINGS)

    @app.post("/api/v1/_test/validation")
    def validation_probe(payload: ValidationPayload) -> dict[str, int]:
        return {"count": payload.count}

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/_test/validation",
            json={"count": "super-secret-value"},
            headers={"X-Request-ID": "validation-id"},
        )

    body = response.json()
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Request-ID"] == "validation-id"
    assert body["code"] == "validation_error"
    assert body["detail"] == "One or more request fields are invalid."
    assert body["request_id"] == "validation-id"
    assert body["errors"][0]["location"] == ["body", "count"]
    assert body["errors"][0]["type"] == "int_parsing"
    assert "super-secret-value" not in response.text


def test_malformed_json_returns_400_invalid_request_without_body_echo() -> None:
    app = create_app(TEST_SETTINGS)

    @app.post("/api/v1/_test/validation")
    def validation_probe(payload: ValidationPayload) -> dict[str, int]:
        return {"count": payload.count}

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/_test/validation",
            content='{"count": "super-secret-value"',
            headers={
                "content-type": "application/json",
                "X-Request-ID": "malformed-json-id",
            },
        )

    body = response.json()
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Request-ID"] == "malformed-json-id"
    assert body["code"] == "invalid_request"
    assert body["title"] == "Invalid request"
    assert body["detail"] == "The request body is not valid JSON."
    assert body["request_id"] == "malformed-json-id"
    assert "errors" not in body
    assert "super-secret-value" not in response.text


def test_controlled_api_errors_use_problem_details() -> None:
    app = create_app(TEST_SETTINGS)

    @app.get("/api/v1/_test/conflict")
    def conflict_probe() -> None:
        raise ApiError(
            status_code=409,
            code="state_conflict",
            title="State conflict",
            detail="The requested transition is not allowed.",
        )

    with TestClient(app) as client:
        response = client.get("/api/v1/_test/conflict")

    body = response.json()
    assert response.status_code == 409
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert body["code"] == "state_conflict"
    assert body["title"] == "State conflict"
    assert body["detail"] == "The requested transition is not allowed."


def test_unhandled_errors_are_generic_and_correlated_in_logs(caplog) -> None:
    app = create_app(TEST_SETTINGS)

    @app.get("/api/v1/_test/crash")
    def crash_probe() -> None:
        raise RuntimeError("private-internal-detail")

    with caplog.at_level(logging.ERROR, logger="agentguard.api.errors"):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(
                "/api/v1/_test/crash", headers={"X-Request-ID": "req-crash"}
            )

    body = response.json()
    assert response.status_code == 500
    assert body["code"] == "internal_error"
    assert body["detail"] == (
        "An unexpected error occurred. Use the request ID to correlate server logs."
    )
    assert body["request_id"] == "req-crash"
    assert response.headers["X-Request-ID"] == "req-crash"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "private-internal-detail" not in response.text
    records = [
        record for record in caplog.records if record.name == "agentguard.api.errors"
    ]
    assert records
    assert records[-1].request_id == "req-crash"


def test_settings_from_env_defaults_to_development(monkeypatch) -> None:
    monkeypatch.setattr("agentguard.api.config.load_dotenv", lambda: None)
    monkeypatch.delenv("AGENTGUARD_API_ENVIRONMENT", raising=False)
    monkeypatch.delenv("AGENTGUARD_API_DOCS_ENABLED", raising=False)

    settings = ApiSettings.from_env()

    assert settings.environment == "development"
    assert settings.docs_enabled is True
    assert settings.version == __version__


@pytest.mark.parametrize(
    ("environment", "expected_docs"),
    [
        ("development", True),
        ("local", True),
        ("test", True),
        ("staging", False),
        ("production", False),
    ],
)
def test_direct_settings_apply_safe_docs_defaults(
    environment: str, expected_docs: bool
) -> None:
    settings = ApiSettings(environment=environment)

    assert settings.docs_enabled is expected_docs


def test_direct_production_docs_override_is_explicit() -> None:
    settings = ApiSettings(environment="production", docs_enabled=True)
    app = create_app(settings)

    assert settings.docs_enabled is True
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_direct_non_development_settings_disable_docs_route(environment: str) -> None:
    app = create_app(ApiSettings(environment=environment))

    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404


def test_settings_from_env_disable_docs_in_production(monkeypatch) -> None:
    monkeypatch.setattr("agentguard.api.config.load_dotenv", lambda: None)
    monkeypatch.setenv("AGENTGUARD_API_ENVIRONMENT", "production")
    monkeypatch.delenv("AGENTGUARD_API_DOCS_ENABLED", raising=False)

    settings = ApiSettings.from_env()

    assert settings.environment == "production"
    assert settings.docs_enabled is False


def test_settings_from_env_accepts_explicit_docs_override(monkeypatch) -> None:
    monkeypatch.setattr("agentguard.api.config.load_dotenv", lambda: None)
    monkeypatch.setenv("AGENTGUARD_API_ENVIRONMENT", "production")
    monkeypatch.setenv("AGENTGUARD_API_DOCS_ENABLED", "no")

    settings = ApiSettings.from_env()

    assert settings.docs_enabled is False

    monkeypatch.setenv("AGENTGUARD_API_DOCS_ENABLED", "yes")
    assert ApiSettings.from_env().docs_enabled is True


@pytest.mark.parametrize(
    "variable,value",
    [
        ("AGENTGUARD_API_ENVIRONMENT", "qa"),
        ("AGENTGUARD_API_DOCS_ENABLED", "sometimes"),
    ],
)
def test_invalid_settings_raise_configuration_error(monkeypatch, variable, value) -> None:
    monkeypatch.setattr("agentguard.api.config.load_dotenv", lambda: None)
    monkeypatch.setenv(variable, value)

    with pytest.raises(ConfigurationError):
        ApiSettings.from_env()
