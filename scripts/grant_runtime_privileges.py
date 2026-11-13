"""Grant the application role its narrow privileges after each Alembic upgrade."""
from __future__ import annotations

import os

from sqlalchemy import create_engine, text


GRANTS = (
    "GRANT SELECT, INSERT, UPDATE ON TABLE executions TO agentguard_app",
    "GRANT SELECT, INSERT ON TABLE execution_events TO agentguard_app",
    "GRANT SELECT, INSERT, UPDATE ON TABLE recovery_sessions TO agentguard_app",
    "GRANT SELECT, INSERT ON TABLE recovery_attempts TO agentguard_app",
    "GRANT SELECT, INSERT, UPDATE ON TABLE recovery_attempt_budgets TO agentguard_app",
)


def main() -> None:
    migration_url = os.environ.get("AGENTGUARD_MIGRATION_DATABASE_URL")
    if not migration_url:
        raise SystemExit("AGENTGUARD_MIGRATION_DATABASE_URL is required")
    engine = create_engine(migration_url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            for statement in GRANTS:
                connection.execute(text(statement))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
