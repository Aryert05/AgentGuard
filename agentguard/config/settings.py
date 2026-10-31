"""Environment-backed settings; credentials are never stored in source."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

SUPPORTED_FAILURE_MODES = {"invalid_arguments", "timeout", "unavailable", "exception"}


class ConfigurationError(ValueError):
    """Raised when an environment-backed configuration value is invalid."""


@dataclass(frozen=True)
class Settings:
    provider: Optional[str]
    model: Optional[str]
    failure_mode: Optional[str]

    def __post_init__(self) -> None:
        validate_failure_mode(self.failure_mode)

    @property
    def llm_configured(self) -> bool:
        return bool(self.provider and self.model)


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        provider=os.getenv("AGENTGUARD_LLM_PROVIDER"),
        model=os.getenv("AGENTGUARD_LLM_MODEL"),
        failure_mode=os.getenv("AGENTGUARD_FAILURE_MODE"),
    )


def validate_failure_mode(value: Optional[str]) -> Optional[str]:
    if value and value not in SUPPORTED_FAILURE_MODES:
        raise ConfigurationError(
            "AGENTGUARD_FAILURE_MODE must be one of: " + ", ".join(sorted(SUPPORTED_FAILURE_MODES))
        )
    return value
