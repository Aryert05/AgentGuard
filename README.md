# AgentGuard

AgentGuard provides an iterative tool-using agent, deterministic offline execution, and structured execution events. Phase 2 adds optional PostgreSQL telemetry persistence and a reproducible synthetic prefix dataset. In provider mode, LangGraph routes model messages through a bounded `ToolNode` loop; offline mode is deterministic and rule-based. See the [Phase 1 guide](docs/phase-01-agent-foundation.md) and [Phase 2 guide](docs/phase-02-telemetry-dataset.md).

```bash
python -m pip install -e '.[dev]'
python -m agentguard --demo "Calculate the total cost of 15 products priced at 250 each"
python -m agentguard --demo "Find information about Tata Motors and summarize the available company data"
python -m pytest
```

## Phase 2: PostgreSQL telemetry and AgentTrace

Install PostgreSQL support with the project dependencies. Copy `.env.example` to `.env` and replace the local placeholders; `.env` is ignored by Git. AgentGuard uses three distinct identities: `POSTGRES_USER` / `AGENTGUARD_BOOTSTRAP_DATABASE_URL` for local bootstrap and role administration, `AGENTGUARD_MIGRATION_DATABASE_URL` for Alembic, and `AGENTGUARD_DATABASE_URL` for application runtime. Runtime persistence is disabled when `AGENTGUARD_DATABASE_URL` is unset; legacy `DATABASE_URL` remains a compatibility fallback. Task descriptions are omitted unless `AGENTGUARD_STORE_TASK_TEXT=true`.

To start the local PostgreSQL 16 Compose service (host port `5433`, loopback only):

```bash
docker compose up -d postgres
docker compose exec -it postgres sh -lc 'exec psql -U "$POSTGRES_USER" -d postgres'
```

In that bootstrap psql session, create the separate migration and runtime roles and set their passwords interactively:

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

Set `AGENTGUARD_MIGRATION_DATABASE_URL` to the migration role and run `alembic upgrade head`. The migration role owns the `public` schema and tables it creates, but has no superuser, database-creation, or role-creation privilege. Then grant the runtime role only the application table operations:

```sql
GRANT SELECT, INSERT, UPDATE ON TABLE executions TO agentguard_app;
GRANT SELECT, INSERT ON TABLE execution_events TO agentguard_app;
```

Run those grants as the migration role after migrations that add tables. The runtime role needs CONNECT and schema USAGE, but not TEMPORARY, table DELETE, event UPDATE, or schema CREATE. Revoking TEMPORARY from PUBLIC removes PostgreSQL's default effective TEMP privilege. If another local user needs temporary tables, grant TEMPORARY directly to that user rather than restoring it to PUBLIC.

Compose publishes only on `127.0.0.1:5433` by default. Its named volume is preserved across container recreation; do not use `docker compose down -v` to fix credentials. For an existing volume, Compose's `POSTGRES_*` variables do not update roles/passwords initialized earlier. Connect using the administrator role already stored in the volume, inspect roles and ownership, and create only missing roles. Before revoking TEMPORARY from PUBLIC in a shared database, inspect other users and explicitly grant TEMPORARY to any that require it. To let the migration role update existing Phase 2 objects, grant it schema CREATE and transfer ownership of the inspected AgentGuard tables and `alembic_version`; do not implicitly change existing passwords or role attributes. Use psycopg v3 URLs and keep the host port aligned with `POSTGRES_HOST_PORT`. The application persists after execution; database errors are returned to the caller. See the Phase 2 guide for full existing-volume and isolated integration-test setup.

Generate the controlled synthetic dataset and run EDA:

```bash
python -m agentguard.dataset.generator --executions 100 --seed 2026 --output data/generated/agenttrace-v1
python analysis/phase_02_eda.py
```

The generator writes a prefix CSV, trace JSONL, manifest, and feature dictionary under `data/generated/agenttrace-v1/`. EDA writes `eda_summary.json` and PNG charts under `analysis/output/agenttrace-v1/`. Both output directories are ignored by Git. The data is synthetic and must not be presented as production telemetry.

For PostgreSQL integration tests, use a separate disposable database and distinct runtime and migration URLs. Create `agentguard_test` once using an administrator; apply migrations using the test migration URL. Grant the test runtime role the same narrow table permissions shown above. Set `AGENTGUARD_TEST_DATABASE_URL` to the runtime role and `AGENTGUARD_TEST_MIGRATION_DATABASE_URL` to the privileged migration/cleanup role:

```bash
docker compose exec postgres sh -lc 'createdb -U "$POSTGRES_USER" agentguard_test'
# Set both URLs in .env; the test uses the runtime role for application SQL.
export AGENTGUARD_TEST_DATABASE_URL='postgresql+psycopg://agentguard_app:<local-placeholder>@localhost:5433/agentguard_test'
export AGENTGUARD_TEST_MIGRATION_DATABASE_URL='postgresql+psycopg://agentguard_migrator:<local-placeholder>@localhost:5433/agentguard_test'
python -m pytest -q tests/integration/test_postgres_persistence.py
```

If `agentguard_test` already exists, skip `createdb` and inspect its privileges. For a new test DB, grant both roles CONNECT, revoke TEMPORARY from PUBLIC on that database, make the migration role the `public` schema owner, and grant runtime schema USAGE. The integration test applies migrations through its migration URL and grants runtime table access using that migration role. It checks effective privileges, including CONNECT, TEMPORARY, schema/table operations, and administrative role attributes. Never point integration tests at a valuable or production database.

The generated dataset is synthetic and small, intended for pipeline experiments rather than evidence of production model quality. For modeling, select only the allow-listed columns in `MODEL_FEATURE_COLUMNS` via `model_feature_matrix`. Do not use IDs or target columns as features. Split samples by `execution_id` at minimum; use `task_template_id` groups when evaluating generalization to unseen task templates. Prefix rows from the same execution share an eventual label and are not independent observations; EDA correlations are descriptive, not inferential.

For provider-backed tool selection, configure `AGENTGUARD_LLM_PROVIDER`, `AGENTGUARD_LLM_MODEL`, and the provider's credential environment variable. The default provider integration dependency is `langchain-openai`; install the relevant LangChain integration if choosing another provider. The app never claims local mock search results are live internet results.

Set `AGENTGUARD_FAILURE_MODE` to `invalid_arguments`, `timeout`, `unavailable`, or `exception` to inject a controlled tool failure. It is disabled when unset. An invalid value is a configuration error. Status is `completed` when all calls succeed, `partially_completed` when at least one succeeds and another call or execution step fails, and `failed` when no tool call succeeds or provider execution fails before producing a usable result.
