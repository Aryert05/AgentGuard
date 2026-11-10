# Phase 4.4 — PostgreSQL Telemetry Integration

**Status:** Implemented and PostgreSQL-verified via disposable test database. No commit or push was made.

## Takeover assessment

The existing Phase 4.1/4.2 implementation already created a SQLAlchemy session factory during the FastAPI lifespan for trace reads, used context-managed sessions for repository reads, bounded and ordered event pagination, and disposed the engine on shutdown. Phase 2 already provided the atomic `persist_execution_trace` repository and database configuration. The only uncommitted PostgreSQL integration work at takeover was an unused `persist` argument on `run_agent`; the API runner did not use it, and the API had no application-owned execution writer. There was no PostgreSQL integration report.

The frozen `docs/phase-04-api-integration-plan.md` identifies diagnosis and recommendations as Phase 4.3 and PostgreSQL integration and hardening as Phase 4.4. This report is named and numbered as Phase 4.4 in accordance with that roadmap. The earlier report-path/heading mismatch was documentation-only; the PostgreSQL implementation was preserved without technical changes during this alignment.

## Implementation

- Preserved the `run_agent(..., persist=True)` compatibility default and existing CLI behavior. When `persist=False`, the runtime does not load database settings or invoke runtime-owned persistence.
- `AgentExecutionRunner` uses `persist=False`; after execution, `ExecutionService` sends the returned state and event collector to an injected writer when database persistence is enabled.
- `DatabaseTraceWriter` delegates to Phase 2 `persist_execution_trace`, including its `session_factory.begin()` transaction, atomic execution/event write, idempotent event insertion, metadata redaction, and sanitized persistence errors.
- The FastAPI lifespan creates one SQLAlchemy session factory only when `AGENTGUARD_DATABASE_URL` (or its legacy fallback) enables persistence. It shares that factory between `DatabaseTraceWriter` and `DatabaseTraceReader` and disposes the engine at shutdown. Repository functions own short-lived transaction/session contexts; no session is retained between requests.
- Offline execution does not create a database engine and does not silently fall back to memory when persistence is enabled. A missing writer fails closed before execution. A write failure returns the existing sanitized `503 persistence_unavailable`; the API does not retry the execution. `persisted=true` is returned only after the repository transaction completes successfully.
- Read retrieval remains database-backed, bounded, ordered by stored `event_index`, and mapped through the existing 404/503 Problem Details behavior.
- No change to `.env.example` was needed: the existing runtime URL and task-text opt-in configure the API, while migrations continue to use the separate migration URL.

## Files changed

- Modified: `README.md`, `agentguard/agent/runtime.py`, `agentguard/api/main.py`, `agentguard/application/execution_service.py`, `agentguard/application/protocols.py`, `tests/unit/test_phase42_api.py`, and `tests/integration/test_postgres_persistence.py`.
- Added: this Phase 4.4 report (previously named `phase-04-3-postgresql-integration-report.md`).
- Preserved existing Phase 4.1/4.2 services and Phase 2 repository/model/migration architecture; no new persistence architecture or schema migration was added.

## Verification

- Focused offline regression run: `.venv/bin/pytest -q tests/unit/test_phase42_api.py tests/unit/test_phase41_api.py tests/unit/test_agent.py tests/unit/test_phase2_database.py` — **111 passed**.
- Complete offline suite: `.venv/bin/pytest -q` — **238 passed, 2 skipped** (the 2 skipped are the PostgreSQL integration tests when test DB environment variables are not set).
- Disposable PostgreSQL verification performed successfully:
  - PostgreSQL 16.15, disposable `postgres:16-alpine` container.
  - Bound to `127.0.0.1:55703`, temporary in-memory data directory.
  - Migrations `0001_execution_telemetry` and `0002_recovery_telemetry` applied.
  - Alembic head: `0002_recovery_telemetry`.
  - `alembic check`: no pending schema operations.
  - Persistence integration tests: 2 passed, 0 skipped.
  - Recovery session/attempt persistence and rollback verified using the restricted runtime role.
  - 24 concurrent independent budget claims against cap 4: exactly 4 succeeded.
  - Stored count was 4; subsequent claim failed.
  - Invalid claim created no budget row.
  - Runtime role restrictions verified, including denied role creation, table creation/drop and audit-table creation.
- `git diff --check` and byte-compilation/import checks passed.

## Limitations

- Budget reservation, external tool execution and telemetry persistence are not atomic together.
- A crash can consume a budget slot without a completed attempt record.
- Exactly-once execution and crash-safe recovery are not established.
- Recovery audit-record operations were not tested because no recovery audit table exists.
- Final Docker inventory verification was unavailable because the local Docker socket denied access.
- The disposable container was explicitly removed and cleanup checks found no remaining test rows.
