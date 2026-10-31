"""Explicit SQLAlchemy engine and session factory construction."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from agentguard.config.database import DatabaseSettings
from agentguard.config.settings import ConfigurationError


def create_session_factory(settings: DatabaseSettings, *, echo: bool = False) -> sessionmaker[Session]:
    if not settings.url:
        raise ConfigurationError("AGENTGUARD_DATABASE_URL is required to create a PostgreSQL session factory")
    try:
        parsed = make_url(settings.url)
        if parsed.drivername == "postgresql":
            parsed = parsed.set(drivername="postgresql+psycopg")
        if parsed.drivername != "postgresql+psycopg":
            raise ConfigurationError("Database connections require the PostgreSQL psycopg v3 driver")
        engine: Engine = create_engine(parsed, pool_pre_ping=True, echo=echo,
                                      hide_parameters=True)
    except ModuleNotFoundError as exc:
        raise ConfigurationError("The PostgreSQL driver is unavailable; install the declared psycopg dependency") from exc
    return sessionmaker(bind=engine, expire_on_commit=False)
