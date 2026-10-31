# Phase 2: Telemetry and Dataset Engineering

Phase 2 adds optional PostgreSQL persistence for completed AgentGuard runs, trace validation/normalization, inference-time prefix features, and the synthetic AgentTrace dataset. This is a development foundation; it is not a production data-retention or model-training system.

## Setup

Install the project dependencies in the chosen Python environment:

```bash
python -m pip install -e '.[dev]'
```

Copy `.env.example` to `.env` and replace every placeholder. Keep `.env` untracked: it contains connection credentials. PostgreSQL binds only to `127.0.0.1:5433` by default. Three roles have separate purposes: `POSTGRES_USER` / `AGENTGUARD_BOOTSTRAP_DATABASE_URL` is the local bootstrap administrator, `AGENTGUARD_MIGRATION_DATABASE_URL` is the non-superuser migration role, and `AGENTGUARD_DATABASE_URL` is the restricted runtime role. `DATABASE_URL` remains a compatibility fallback, but new setups should use the explicit runtime variable.

```dotenv
AGENTGUARD_DATABASE_URL=postgresql+psycopg://agentguard_app:<local-placeholder>@localhost:5433/agentguard
AGENTGUARD_MIGRATION_DATABASE_URL=postgresql+psycopg://agentguard_migrator:<local-placeholder>@localhost:5433/agentguard
AGENTGUARD_STORE_TASK_TEXT=false
```

Without `AGENTGUARD_DATABASE_URL`, the offline deterministic runner does not connect to a database. Task descriptions are omitted by default. If task-text storage is explicitly enabled, common credential patterns are redacted and the value is capped at 2,000 characters; this is not comprehensive sensitive-data detection.

Start the local PostgreSQL 16 service and apply the Alembic migration. Compose publishes host port `5433` by default, avoiding a native PostgreSQL listener on `localhost:5432`; keep `POSTGRES_HOST_PORT` and the URL port aligned.

```bash
docker compose up -d postgres
docker compose exec -it postgres sh -lc 'exec psql -U "$POSTGRES_USER" -d postgres'
```

The bootstrap psql session is for role administration only. Create separate non-superuser migration and runtime roles and set their passwords interactively:

```sql
CREATE ROLE agentguard_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
\password agentguard_migrator
CREATE ROLE agentguard_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
\password agentguard_app
GRANT CONNECT ON DATABASE agentguard TO agentguard_migrator, agentguard_app;
\connect agentguard
REVOKE TEMPORARY ON DATABASE agentguard FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO agentguard_migrator;
GRANT USAGE ON SCHEMA public TO agentguard_app;
```

Run Alembic using `AGENTGUARD_MIGRATION_DATABASE_URL`. The migration role owns the schema and the tables it creates, so it can apply future migrations and grant table access without owning the database or having SUPERUSER, CREATEDB, or CREATEROLE. Then grant runtime access to the existing application tables as the migration role:

```sql
GRANT SELECT, INSERT, UPDATE ON TABLE executions TO agentguard_app;
GRANT SELECT, INSERT ON TABLE execution_events TO agentguard_app;
```

The application runtime needs CONNECT, schema USAGE, and those table permissions. It does not need schema CREATE, database CREATE, TEMPORARY, event UPDATE, table DELETE, sequences, or role-management privileges. `REVOKE TEMPORARY ON DATABASE agentguard FROM PUBLIC` removes PostgreSQL's default effective TEMP privilege while explicit CONNECT grants preserve access. Check other users before revoking TEMPORARY on any shared database and grant it directly to specific roles that need it.

For an already initialized named volume, Compose's `POSTGRES_*` values do not change existing roles/passwords because PostgreSQL only uses them when initializing an empty data directory. Do not delete/recreate the volume or assume changing `POSTGRES_USER` changes the stored bootstrap role. Connect as the administrator role that already exists, inspect role membership and object ownership, and create only missing roles. After confirming these AgentGuard objects exist and are owned as expected, a bootstrap administrator can provision the existing schema without changing application data:

```sql
GRANT CONNECT ON DATABASE agentguard TO agentguard_migrator, agentguard_app;
\connect agentguard
REVOKE TEMPORARY ON DATABASE agentguard FROM PUBLIC;
GRANT CREATE ON SCHEMA public TO agentguard_migrator;
ALTER SCHEMA public OWNER TO agentguard_migrator;
ALTER TABLE IF EXISTS public.executions OWNER TO agentguard_migrator;
ALTER TABLE IF EXISTS public.execution_events OWNER TO agentguard_migrator;
ALTER TABLE IF EXISTS public.alembic_version OWNER TO agentguard_migrator;
GRANT USAGE ON SCHEMA public TO agentguard_app;
GRANT SELECT, INSERT, UPDATE ON TABLE executions TO agentguard_app;
GRANT SELECT, INSERT ON TABLE execution_events TO agentguard_app;
```

Create roles only when inspection confirms they are missing, and set their passwords interactively. Keep existing credentials unchanged unless an administrator explicitly rotates them. Do not alter unrelated roles. The `TEMPORARY` revocation affects PUBLIC; check other users first and grant TEMPORARY directly to specific roles that need it. Explicit CONNECT grants preserve required database access.

Create a separate integration-test database once using an administrator. Set the test runtime and migration URLs separately:

```bash
docker compose exec postgres sh -lc 'createdb -U "$POSTGRES_USER" agentguard_test'
export AGENTGUARD_TEST_DATABASE_URL='postgresql+psycopg://agentguard_app:<local-placeholder>@localhost:5433/agentguard_test'
export AGENTGUARD_TEST_MIGRATION_DATABASE_URL='postgresql+psycopg://agentguard_migrator:<local-placeholder>@localhost:5433/agentguard_test'
```

The roles are cluster-wide, but database privileges and schema ownership must also be set for the test database. As bootstrap, grant CONNECT to both roles, connect to `agentguard_test`, revoke TEMPORARY from PUBLIC, set the schema owner, and grant runtime schema USAGE:

```sql
GRANT CONNECT ON DATABASE agentguard_test TO agentguard_migrator, agentguard_app;
\connect agentguard_test
REVOKE TEMPORARY ON DATABASE agentguard_test FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO agentguard_migrator;
GRANT USAGE ON SCHEMA public TO agentguard_app;
```

If `agentguard_test` already exists, skip `createdb` and inspect its ownership/privileges first. The integration test runs Alembic using the test migration URL, grants the runtime table access from the migration role, and cleans up its uniquely identified execution rows with that role. Application operations use the runtime role. It verifies effective database/schema/table access and denied DDL. Do not point it at a valuable or production database.

### Existing volumes and missing-role troubleshooting

Compose initialization variables (`POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`) apply only when PostgreSQL initializes an empty data directory. Editing these values does not alter users or databases inside an existing named volume. Preserve the volume; do not run `docker compose down -v`. Connect using the database's existing administrative role, inspect roles/databases, and create or alter only what is missing. If an expected role is reported missing while `docker compose exec postgres` shows it exists, check the endpoint: a native host PostgreSQL server may be answering on `localhost:5432` while the Compose container is published on another port. Confirm with `docker compose ps` and use the matching host port in both URLs.

If the password placeholder in `.env.example` differs from the password already stored for the role in an existing volume, keep the existing password in local `.env` or deliberately rotate it while connected as an administrator. Changing the Compose environment value alone does not rotate the database role password.

## Persistence and trace processing

`run_agent` returns the `ExecutionState` and `EventCollector` after deterministic or provider-backed execution. If database settings are enabled, the telemetry adapter writes the execution and ordered events in one transaction. Exact event retries are ignored; event ID reuse with changed content and event-order collisions raise `TelemetryPersistenceError`. Persistence failures are surfaced to the caller. Retrieval returns the execution plus events ordered by stored `event_index`.

The processor validates execution/task identity, parses records, normalizes timestamps to UTC, removes identical duplicate event IDs, rejects conflicting duplicate IDs, orders by timestamp, and marks traces incomplete unless one start event and one terminal event bound the trace. Raw trace mappings must explicitly provide `event_id` and `timestamp`; live event construction retains generated defaults. Retrieval and normalization are separate operations.

## AgentTrace generation

Generate controlled synthetic traces and prefix samples:

```bash
python -m agentguard.dataset.generator --executions 100 --seed 2026 --output data/generated/agenttrace-v1
```

Outputs are `agenttrace_prefixes.csv`, `agenttrace_traces.jsonl`, `manifest.json`, and `feature_dictionary.json`. The manifest records the seed, scenario distribution, counts, provenance, and feature schema version. Repeated generation with the same seed yields identical dataset rows and trace JSONL; the manifest includes a wall-clock generation timestamp.

Scenarios include success, invalid arguments, timeout, unavailable tool, exception, repeated failure, and partial completion. Every prefix excludes execution terminal events. Final outcome labels are attached to each prefix: completed is `eventual_failure=0`; failed and partially completed are `1`; unknown outcomes remain unlabeled.

### Feature definitions

`model_feature_matrix` selects only these inference-time features, in the following order. IDs and all target columns are excluded by the allow-list.

| Feature | Type | Definition |
|---|---|---|
| `prefix_length` | integer | Number of events observed in the prefix. |
| `elapsed_time_ms` | float/null | Last observed timestamp minus first; null for fewer than two events. |
| `current_step_number` | integer | Step number on the last observed event. |
| `total_tool_calls_so_far` | integer | Count of observed `tool_selected` events. |
| `successful_tool_calls` | integer | Count of observed `tool_completed` events. |
| `failed_tool_calls` | integer | Count of observed `tool_failed` events. |
| `tool_failure_rate_so_far` | float/null | Failed outcomes / (failed + completed outcomes); null before any tool outcome. |
| `average_tool_duration_ms` | float/null | Mean observed non-null duration on completed/failed events. |
| `maximum_tool_duration_ms` | float/null | Maximum observed non-null duration on completed/failed events. |
| `repeated_tool_call_count` | integer | Selected tool calls minus distinct selected tool names. |
| `distinct_tool_count` | integer | Number of distinct tool names selected. |
| `timeout_count` | integer | Observed failed events identified by `TimeoutError` or timeout wording. |
| `invalid_argument_count` | integer | Observed failed events identified by validation or invalid-argument wording. |
| `tool_unavailable_count` | integer | Observed failed events identified by `ConnectionError` or unavailable wording. |
| `consecutive_failure_count` | integer | Trailing failed tool outcomes, considering completed/failed outcomes. |
| `time_since_last_failure_ms` | float/null | Last prefix timestamp minus most recent observed failed-event timestamp; null without one. |
| `tool_switch_count` | integer | Adjacent changes in selected tool name. |
| `repeated_failure_pattern_count` | integer | Repeated observed `error_type` occurrences after the first. |

There is one distinct-tool count; the former redundant `unique_tools_used` alias was removed. Undefined numeric measurements are `None` in memory and blank/NaN when read from CSV. Count features use zero when no matching event has been observed.

Target columns are `eventual_failure`, `outcome_label`, and `failure_category`. They describe eventual execution outcomes and must not enter a model feature matrix. `execution_id`, `task_id`, `task_template_id`, and `prefix_id` are identifiers/grouping metadata, not features. Group train/test partitions by `execution_id` at minimum. Group by `task_template_id` as well when measuring generalization to unseen templates; never split prefixes from one execution across train and test.

## EDA

Run the existing EDA script after generation:

```bash
python analysis/phase_02_eda.py
```

It writes `eda_summary.json`, `execution_outcomes.png`, `failure_categories.png`, `tool_usage.png`, `event_frequency.png`, `execution_duration.png`, `prefix_length.png`, and `feature_failure_relationships.png` under `analysis/output/agenttrace-v1/`. The dataset is synthetic and small. Prefix rows from one execution share eventual-outcome labels and are not independent observations. Feature-to-label correlations are descriptive only, not inferential evidence or production model-quality measurements.

## Privacy and security

- `AGENTGUARD_DATABASE_URL` is the runtime URL (with `DATABASE_URL` retained as a compatibility fallback); `AGENTGUARD_MIGRATION_DATABASE_URL` is separate and must not be placed in the runtime service. Do not commit `.env` or use placeholders as credentials.
- Compose publishes PostgreSQL only on loopback by default. The bootstrap/admin role is for local initialization and role administration, the migration role is for Alembic, and AgentGuard runtime uses the restricted application role.
- Task text is not persisted unless `AGENTGUARD_STORE_TASK_TEXT=true`.
- Event error messages are persisted. Event validation and persistence-boundary metadata sanitization redact common token/password/Bearer/API-key patterns, but regex redaction can miss secrets and does not identify arbitrary PII.
- A new engine is created and disposed per persisted execution. This keeps the prototype lifecycle explicit without an application shutdown hook; a long-running service should own and reuse an engine/pool at application lifespan.
- Database access controls, encryption, retention/deletion policy, backups, and production credential management remain deployment responsibilities.

## Verification commands

```bash
python -m pytest -q
python -m pytest -q tests/unit/test_agent.py
python -m pytest -q tests/unit/test_phase2_database.py tests/unit/test_phase2_processing.py tests/unit/test_phase2_dataset.py
python -m pytest -q tests/integration/test_postgres_persistence.py
```

PostgreSQL integration verification requires a reachable disposable PostgreSQL database. A skipped integration test is not evidence that database persistence works.
