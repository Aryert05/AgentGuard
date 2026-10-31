"""Small, conservative redaction helpers for persisted telemetry."""
from __future__ import annotations

import re
from typing import Any


_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|authorization|password|secret|token)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)\bBearer\s+[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)


def redact_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = _PATTERNS[0].sub(r"\1=[REDACTED]", value)
    value = _PATTERNS[1].sub("Bearer [REDACTED]", value)
    return _PATTERNS[2].sub("[REDACTED]", value)


def safe_task_description(value: str, *, limit: int = 2000) -> str:
    """Store a useful, bounded task description with common credential forms removed."""
    return (redact_text(value) or "")[:limit]


def redact_metadata(value: Any) -> Any:
    """Return a sanitized copy of JSON-like metadata, including nested values."""
    if isinstance(value, dict):
        sensitive = ("api_key", "access_token", "authorization", "credential", "password", "secret", "token")
        return {key: "[REDACTED]" if any(name in str(key).lower() for name in sensitive)
                else redact_metadata(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_metadata(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
