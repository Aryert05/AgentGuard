#!/bin/sh
set -eu

: "${AGENTGUARD_MIGRATION_PASSWORD:?AGENTGUARD_MIGRATION_PASSWORD is required}"
: "${AGENTGUARD_APP_PASSWORD:?AGENTGUARD_APP_PASSWORD is required}"

# These role passwords are also interpolated into SQLAlchemy URLs by Compose.
# Restrict them to URL-safe characters rather than risking ambiguous URL parsing.
case "${AGENTGUARD_MIGRATION_PASSWORD}${AGENTGUARD_APP_PASSWORD}" in
  *[!A-Za-z0-9_-]*)
    echo "Use URL-safe alphanumeric, underscore, or hyphen characters for role passwords" >&2
    exit 1
    ;;
esac

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=database_name="$POSTGRES_DB" \
  --set=migration_password="$AGENTGUARD_MIGRATION_PASSWORD" \
  --set=app_password="$AGENTGUARD_APP_PASSWORD" <<'SQL'
CREATE ROLE agentguard_migrator LOGIN PASSWORD :'migration_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE agentguard_app LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO agentguard_migrator, agentguard_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE, TEMPORARY ON DATABASE :"database_name" FROM PUBLIC;
ALTER SCHEMA public OWNER TO agentguard_migrator;
GRANT USAGE ON SCHEMA public TO agentguard_app;
SQL
