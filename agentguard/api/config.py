"""Environment-backed configuration for the AgentGuard API."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from agentguard import __version__
from agentguard.config.settings import ConfigurationError

SUPPORTED_ENVIRONMENTS = frozenset(
    {"development", "local", "test", "staging", "production"}
)
_DOCS_ENVIRONMENTS = frozenset({"development", "local", "test"})


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


@dataclass(frozen=True)
class ApiSettings:
    service_name: str = "AgentGuard API"
    version: str = __version__
    environment: str = "development"
    docs_enabled: bool | None = None
    description: str = "AI Agent Reliability & Failure Intelligence Platform API"

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
        return cls(environment=environment, docs_enabled=docs_enabled)
