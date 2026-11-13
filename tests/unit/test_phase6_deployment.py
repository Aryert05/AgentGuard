from __future__ import annotations

import pytest
from fastapi.middleware.cors import CORSMiddleware

from agentguard.api.config import ApiSettings
from agentguard.api.main import create_app
from agentguard.config.settings import ConfigurationError


def test_development_cors_is_limited_to_local_frontend_origins() -> None:
    app = create_app(ApiSettings(environment="development"))
    cors = next(
        middleware for middleware in app.user_middleware
        if middleware.cls is CORSMiddleware
    )

    assert cors.kwargs["allow_origins"] == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    assert cors.kwargs["allow_credentials"] is False


def test_production_cors_is_disabled_by_default() -> None:
    app = create_app(ApiSettings(environment="production"))

    assert not any(middleware.cls is CORSMiddleware for middleware in app.user_middleware)


@pytest.mark.parametrize(
    "origins",
    [("*",), ("null",), ("https://example.test/path",), ("ftp://example.test",)],
)
def test_cors_rejects_wildcard_or_non_origin_values(origins: tuple[str, ...]) -> None:
    with pytest.raises(ConfigurationError, match=r"exact HTTP\(S\) origins"):
        ApiSettings(environment="production", cors_origins=origins)


def test_configured_cors_accepts_exact_https_origin() -> None:
    settings = ApiSettings(
        environment="production",
        cors_origins=("https://dashboard.example.test",),
    )

    assert settings.cors_origins == ("https://dashboard.example.test",)
