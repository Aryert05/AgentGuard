"""Environment-backed configuration for the AgentGuard API."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import load_dotenv

from agentguard import __version__
from agentguard.config.settings import ConfigurationError

SUPPORTED_ENVIRONMENTS = frozenset(
    {"development", "local", "test", "staging", "production"}
)
_DOCS_ENVIRONMENTS = frozenset({"development", "local", "test"})
_LOCAL_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _parse_origins(value: str) -> tuple[str, ...]:
    origins = tuple(dict.fromkeys(part.strip().rstrip("/") for part in value.split(",") if part.strip()))
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            parsed.port  # Validate malformed port values as part of the origin.
        except ValueError as exc:
            raise ConfigurationError(
                "AGENTGUARD_CORS_ORIGINS must be a comma-separated list of exact HTTP(S) origins"
            ) from exc
        if (
            origin == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigurationError(
                "AGENTGUARD_CORS_ORIGINS must be a comma-separated list of exact HTTP(S) origins"
            )
    return origins


@dataclass(frozen=True)
class ApiSettings:
    service_name: str = "AgentGuard API"
    version: str = __version__
    environment: str = "development"
    docs_enabled: bool | None = None
    description: str = "AI Agent Reliability & Failure Intelligence Platform API"
    cors_origins: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not self.service_name.strip():
            raise ConfigurationError("API service name must not be empty")
        if not self.version.strip():
            raise ConfigurationError("API version must not be empty")
        if self.environment not in SUPPORTED_ENVIRONMENTS:
            raise ConfigurationError(
                "AGENTGUARD_API_ENVIRONMENT must be one of: "
                + ", ".join(sorted(SUPPORTED_ENVIRONMENTS))
            )
        if self.docs_enabled is None:
            object.__setattr__(
                self, "docs_enabled", self.environment in _DOCS_ENVIRONMENTS
            )
        if self.cors_origins is None:
            object.__setattr__(
                self,
                "cors_origins",
                _LOCAL_CORS_ORIGINS if self.environment in _DOCS_ENVIRONMENTS else (),
            )
        else:
            object.__setattr__(self, "cors_origins", _parse_origins(",".join(self.cors_origins)))

    @classmethod
    def from_env(cls) -> "ApiSettings":
        load_dotenv()
        environment = (
            os.getenv("AGENTGUARD_API_ENVIRONMENT", "development").strip().lower()
            or "development"
        )
        docs_value = os.getenv("AGENTGUARD_API_DOCS_ENABLED")
        docs_enabled = (
            None
            if docs_value is None
            else _parse_bool("AGENTGUARD_API_DOCS_ENABLED", docs_value)
        )
        origins_value = os.getenv("AGENTGUARD_CORS_ORIGINS")
        cors_origins = None if origins_value is None else _parse_origins(origins_value)
        return cls(
            environment=environment,
            docs_enabled=docs_enabled,
            cors_origins=cors_origins,
        )
