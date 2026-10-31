"""PostgreSQL configuration, validated without opening a connection."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.engine import make_url
from dotenv import load_dotenv

from agentguard.config.settings import ConfigurationError


@dataclass(frozen=True)
class DatabaseSettings:
    url: Optional[str] = field(repr=False)
    store_task_text: bool = False

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        load_dotenv()
        # Prefer the explicit runtime credential name; keep DATABASE_URL as a
        # compatibility fallback for existing local installations.
        value = os.getenv("AGENTGUARD_DATABASE_URL") or os.getenv("DATABASE_URL")
        if value:
            try:
                parsed = make_url(value)
            except Exception as exc:
                raise ConfigurationError("AGENTGUARD_DATABASE_URL is not a valid SQLAlchemy URL") from exc
            if any(character.isspace() for character in value) or not parsed.database:
                raise ConfigurationError("AGENTGUARD_DATABASE_URL is not a valid SQLAlchemy URL")
            if parsed.drivername not in {"postgresql", "postgresql+psycopg"}:
                raise ConfigurationError("AGENTGUARD_DATABASE_URL must use PostgreSQL (postgresql+psycopg recommended)")
            # SQLAlchemy's plain postgresql:// default may select psycopg2.
            # Normalize explicitly so the declared psycopg v3 driver is used.
            if parsed.drivername == "postgresql":
                value = parsed.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
        store_raw = os.getenv("AGENTGUARD_STORE_TASK_TEXT", "false").strip().lower()
        if store_raw not in {"true", "false", "1", "0", "yes", "no"}:
            raise ConfigurationError("AGENTGUARD_STORE_TASK_TEXT must be true or false")
        return cls(url=value, store_task_text=store_raw in {"true", "1", "yes"})

    @property
    def enabled(self) -> bool:
        return bool(self.url)
