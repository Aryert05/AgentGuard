# AgentGuard

AgentGuard provides an iterative tool-using agent, deterministic offline execution, and structured execution events. Phase 2 adds optional PostgreSQL telemetry persistence and a reproducible synthetic prefix dataset. In provider mode, LangGraph routes model messages through a bounded `ToolNode` loop; offline mode is deterministic and rule-based. See the [Phase 1 guide](docs/phase-01-agent-foundation.md) and [Phase 2 guide](docs/phase-02-telemetry-dataset.md).

```bash
python -m pip install -e .
python -m agentguard --demo "Calculate the total cost of 15 products priced at 250 each"
python -m agentguard --demo "Find information about Tata Motors and summarize the available company data"
```

## Phase 4 API

Phase 4.1 adds a FastAPI application factory with versioned routes under `/api/v1`. Phase 4.2 adds synchronous agent execution and persisted trace retrieval, Phase 4.3 adds diagnosis and informational recovery recommendations, and Phase 4.4 integrates PostgreSQL persistence and retrieval. Phase 4.5 adds cross-component end-to-end regression coverage and hardening. Execution submission uses the existing `run_agent` path; it does not introduce a second agent implementation. Recommendations remain informational: the API does not execute recovery actions.

```bash
python -m pip install -e '.[dev,api]'
uvicorn agentguard.api.main:create_app --factory --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/api/v1/health

curl -X POST http://127.0.0.1:8000/api/v1/executions \
  -H 'Content-Type: application/json' \
  -d '{"task":"Calculate the total cost of 15 products priced at 250 each","mode":"deterministic","max_tool_calls":8}'

curl http://127.0.0.1:8000/api/v1/executions/<execution_id>
curl 'http://127.0.0.1:8000/api/v1/executions/<execution_id>/events?limit=100'

python -m pytest tests/unit/test_phase41_api.py tests/unit/test_phase42_api.py tests/unit/test_phase43_api.py tests/unit/test_phase45_api.py
```

`POST /api/v1/executions` is synchronous and non-idempotent; do not retry it automatically. Agent-level `failed` or `partially_completed` results still return `201`; inspect `status`. When persistence is disabled, execution returns `persisted=false` and the summary/event routes return `503 persistence_not_configured`. LLM mode without a configured provider and model returns `503 llm_not_configured`. Requests are bounded to an 8,000-character task and `max_tool_calls` `1..8`; event pages accept `limit` `1..500` and the `event_index` cursor. Public responses omit messages, raw tool arguments/results, internal state, and secrets.

When `AGENTGUARD_DATABASE_URL` (or the legacy `DATABASE_URL`) is configured, the API creates one SQLAlchemy session factory during startup and shares it between execution persistence and trace retrieval; it disposes the engine at shutdown. The API calls the existing agent runtime with runtime-owned persistence disabled, then writes the returned execution and events once through the Phase 2 repository transaction. `persisted=true` is returned only after that transaction commits. Database failures return `503 persistence_unavailable`; execution is not retried. With no database URL, no engine is created and offline execution remains available. Apply schema changes separately using `AGENTGUARD_MIGRATION_DATABASE_URL` and `alembic upgrade head`; runtime credentials retain the Phase 2 least-privilege grants.

### Diagnosis and recommendations

`POST /api/v1/diagnoses` accepts `{"events": [...]}` and returns the Phase 3.4 diagnosis plus informational recovery recommendations. Events use the existing `ExecutionEvent` schema and prefix validation. The request can contain at most 1,000 events; an empty list is valid. `POST /api/v1/executions/{execution_id}/diagnoses` diagnoses a persisted event prefix using a bounded, ordered read of at most 1,000 events. It returns `404 execution_not_found` for an unknown execution, `503 persistence_unavailable` when persistence is disabled or unavailable, and `413 diagnosis_trace_too_large` if the stored trace exceeds that cap.

Both routes reuse `DiagnosticService.analyze` and return API-specific response schemas. Clients cannot provide `ml_signal`; an optional scorer can be injected server-side through `create_app(ml_scorer=...)`. If scoring fails, rule-based diagnosis is returned with a generic `ml_error`; internal exception details are not exposed. Recommendations are informational only: these routes do not execute tools, invoke recovery controllers/executors, or persist diagnoses or recovery sessions.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/diagnoses \
  -H 'Content-Type: application/json' \
  -d '{"events":[{"event_id":"evt-1","execution_id":"exec-1","task_id":"task-1","step_number":0,"timestamp":"2026-11-02T10:00:00Z","event_type":"execution_started","component":"agent","status":"running","metadata":{}}]}'

curl -X POST http://127.0.0.1:8000/api/v1/executions/<execution_id>/diagnoses
```

The response contains `diagnosis`, `recommendations`, and nullable `ml_error`. Structurally invalid events use the standard `422 validation_error`; semantically invalid prefixes return `422 invalid_diagnosis_prefix`. See the [Phase 4.3 diagnosis report](docs/reports/phase-04-3-diagnosis-recommendations-report.md), [Phase 4.4 PostgreSQL report](docs/reports/phase-04-4-postgresql-integration-report.md), and [Phase 4.5 end-to-end report](docs/reports/phase-04-5-end-to-end-testing-report.md) for implementation details and verification limits.

Interactive docs are available at `http://127.0.0.1:8000/docs` when enabled. Docs default on in `development`, `local`, and `test`, and off in `staging` and `production`, including direct `ApiSettings` construction. Enabling docs in staging or production is a deliberate override: set `AGENTGUARD_API_DOCS_ENABLED=true` only for controlled use, or pass `docs_enabled=True` to `ApiSettings` when constructing the app directly. `AGENTGUARD_API_ENVIRONMENT` validates the environment label reported by `/api/v1/health`. The liveness endpoint deliberately does not check PostgreSQL or any other external dependency. The API is intended for local, single-tenant use; authentication, rate limiting, and body-size middleware remain deployment requirements before public exposure.

For development and tests, install the development extra. Phase 3 ML preparation tests and sklearn preprocessing additionally require the optional ML extra:

```bash
python -m pip install -e '.[dev]'
python -m pytest

# Phase 3.2 data preparation, split, and sklearn preprocessing work/tests
python -m pip install -e '.[dev,ml]'
python -m pytest tests/unit/test_phase3_data.py tests/unit/test_phase3_splitting.py tests/unit/test_phase3_preprocessing.py

# Phase 3.3 train-only fitting, grouped analysis, and validation-only threshold selection
python -m pytest tests/unit/test_phase3_evaluation.py tests/unit/test_phase3_training.py tests/unit/test_phase3_analysis.py
python -m agentguard.ml.train --strategy execution_held_out
python -m agentguard.ml.train --strategy task_template_held_out
```

The base `agentguard.ml.data` loader and group-splitting utilities do not import scikit-learn. Importing or using ML models and preprocessing requires the `ml` extra. Use `fit_transform_partitions` for AgentTrace data so the dataset/split binding is checked before preprocessing; `build_preprocessing_pipeline` is a raw sklearn pipeline factory and cannot enforce split provenance when fitted directly. The Phase 3.3 commands train baselines on grouped training partitions, select thresholds on validation data, and write JSON artifacts under the ignored `data/generated/` directory. The execution-held-out run is primary; the task-template-held-out run is exploratory. Both include execution/template-balanced metrics, cluster-bootstrap intervals, and prefix/time strata, write a SHA-256 sidecar, and record source-file/code-snapshot provenance. They do not score the test partitions or save model binaries. Results are preliminary and use a small synthetic dataset.

Phase 3.4 exposes deterministic diagnosis and bounded recovery interfaces in `agentguard.diagnostics`. Call `DiagnosticService.analyze(events, ml_signal=...)` with an observed event prefix to get evidence and recommendations. `DiagnosticRecoveryService` can pass a recommendation to a caller-supplied one-action executor under `RecoveryPolicy` limits. Approval-gated/high-risk actions are returned without execution. The default in-memory attempt ledger belongs to one controller; inject one shared `InMemoryRecoveryAttemptLedger` instance to coordinate controllers in one process. For durable cross-process and restart-safe enforcement, provide the same `DatabaseRecoveryTelemetry(session_factory)` as both the controller's telemetry sink and attempt ledger; database concurrency has not been verified in this project environment. Migration `0002_recovery_telemetry` adds session, attempt, and budget tables. This is a Python service boundary: the agent runtime does not automatically launch recovery.

After applying migration `0002_recovery_telemetry`, grant the restricted runtime role only the recovery-table permissions needed by the persistence repository:

```sql
GRANT SELECT, INSERT, UPDATE ON TABLE recovery_sessions TO agentguard_app;
GRANT SELECT, INSERT ON TABLE recovery_attempts TO agentguard_app;
GRANT SELECT, INSERT, UPDATE ON TABLE recovery_attempt_budgets TO agentguard_app;
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
