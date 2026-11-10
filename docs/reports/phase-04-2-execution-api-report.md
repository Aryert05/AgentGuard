# Phase 4.2 — Execution API and Trace Retrieval Implementation Report

## Scope

Implemented Phase 4.2 on branch `phase/04-api-integration`: synchronous agent execution through `POST /api/v1/executions`, persisted execution summary retrieval, and bounded persisted event retrieval. The route and application layers reuse the existing `run_agent` runtime and repository functions; no second agent implementation was added. Diagnostics, ML inference, recovery endpoints, database readiness, migrations, Docker/PostgreSQL integration runs, and Phase 4.3 work were not added.

## Plan deviation

The frozen plan proposes `/v1` paths and readiness endpoints. Phase 4.1 established the instruction-mandated `/api/v1` prefix and a liveness-only health endpoint, and Phase 4.2 follows that established contract. The frozen plan file was not modified.

Request body-size middleware remains deferred. The execution request is bounded semantically by task length (`8,000` characters) and `max_tool_calls` (`1..8`), but there is no global request-body byte limit yet.

## Architecture

- API routes remain thin: `agentguard/api/routes/executions.py` validates UUIDs/query bounds, delegates to application services, and declares explicit response models.
- Application orchestration lives in `agentguard/application/execution_service.py`, `trace_service.py`, and `protocols.py`. `AgentExecutionRunner` adapts the existing `run_agent` call; `ExecutionService` handles LLM prechecks, persistence failures, initialization failures, and public response mapping.
- `TraceService` owns persistence-disabled checks, missing execution mapping, and summary/event redaction. `DatabaseTraceReader` is the production adapter over repository functions; fakes can replace both protocols in tests.
- `AppServices` now includes execution and trace services. Default services are constructed in the lifespan from `ApiSettings`, `Settings`, and `DatabaseSettings`; a read session factory is created only when persistence is enabled and is disposed on shutdown.
- API schemas are explicit Pydantic DTOs and do not subclass internal domain models. Public execution responses omit messages, raw tool arguments/results, and internal state.
- `ApplicationError` mappings produce the existing Problem Details envelope. Stable codes include `execution_not_found`, `persistence_not_configured`, `persistence_unavailable`, `llm_not_configured`, and `execution_unavailable`.
- `retrieve_execution_events` is a new read-only repository helper that orders by stored `event_index`, fetches `limit + 1`, and returns the next cursor without loading the complete trace.
- Lazy API-schema imports avoid an API/application import cycle while keeping runtime imports explicit.

## Files added/modified

Added:

- `agentguard/application/__init__.py`
- `agentguard/application/errors.py`
- `agentguard/application/protocols.py`
- `agentguard/application/execution_service.py`
- `agentguard/application/trace_service.py`
- `agentguard/api/routes/executions.py`
- `agentguard/api/schemas/execution.py`
- `tests/unit/test_phase42_api.py`
- `docs/reports/phase-04-2-execution-api-report.md`

Modified:

- `agentguard/api/dependencies.py`
- `agentguard/api/errors.py`
- `agentguard/api/main.py`
- `agentguard/api/router.py`
- `agentguard/api/schemas/__init__.py`
- `agentguard/database/repositories.py`
- `README.md`

Unchanged: `docs/phase-04-api-integration-plan.md`.

## Endpoint specification

### `POST /api/v1/executions`

Returns `201 Created` for a completed agent request, including agent-level `failed` and `partially_completed` results. The endpoint is synchronous and non-idempotent.

Request fields:

- `task`: required, non-blank, maximum `8,000` characters.
- `mode`: `deterministic` or `llm`; defaults to `deterministic`.
- `max_tool_calls`: `1..8`; defaults to the existing `MAX_TOOL_CALLS`.
- Extra fields are rejected.

Observed deterministic response for the documented calculator task:

```json
{
  "execution_id": "3f6ca8df-8d36-447f-b645-f18a8bcad38c",
  "task_id": "4a5cce62-73e7-4343-8413-490c8189ead2",
  "status": "completed",
  "mode": "deterministic",
  "started_at": "2026-11-02T15:55:26.473657Z",
  "completed_at": "2026-11-02T15:55:26.474704Z",
  "duration_ms": 1.047,
  "successful_tool_call_count": 1,
  "failed_tool_call_count": 0,
  "tool_calls": [
    {"tool_name": "calculator", "status": "completed", "duration_ms": null}
  ],
  "final_output": "{'operation': 'multiply', 'a': 15.0, 'b': 250.0, 'result': 3750.0}",
  "persisted": false
}
```

`final_output` is the existing runtime representation; it is not normalized to a bare numeric string.

### `GET /api/v1/executions/{execution_id}`

Returns `200` with the persisted execution summary. It returns `404 execution_not_found` for an unknown UUID and `503 persistence_not_configured` when persistence is disabled. It does not return `final_output` or invent data that was not persisted.

### `GET /api/v1/executions/{execution_id}/events`

Returns `200` with an ordered event page:

```json
{
  "execution_id": "uuid",
  "events": [],
  "next_after_event_index": null
}
```

Query bounds:

- `limit`: `1..500`, default `100`.
- `after_event_index`: optional non-negative integer; returns events strictly after that stored index.

The response includes persisted event fields with sanitized error text and redacted metadata. Unknown execution returns `404`; persistence disabled returns `503`.

## Execution and persistence semantics

- `mode=deterministic` calls `run_agent(task, demo=True, ...)`.
- `mode=llm` calls `run_agent(task, demo=False, ...)` only after a configured-provider precheck.
- The API does not call persistence a second time; the existing runtime write path remains authoritative.
- `TelemetryPersistenceError` becomes a sanitized `503 persistence_unavailable`; the API does not retry the agent.
- `ConfigurationError`, `ImportError`, and a `RuntimeError` caused by an `ImportError` map to `503 execution_unavailable`.
- Other unexpected runtime errors remain generic `500 internal_error` responses.
- With persistence disabled, execution succeeds with `persisted=false`; persistence-backed retrieval returns `503`.
- Persistence-enabled runs depend on the existing PostgreSQL path. Tests use fakes and a local SQLite schema only for the repository pagination helper; PostgreSQL was not exercised.

## Validation and security

- Task length and blankness are enforced at the API boundary.
- Tool-call and pagination limits are bounded.
- UUID path parameters reject invalid identifiers before service access.
- Public responses omit raw tool arguments, tool results, messages, and internal state.
- Final output, task descriptions, event error text, and metadata pass through existing privacy helpers where applicable.
- Database/provider exception text is never returned; internal defects return generic details with request IDs.
- Problem Details, request-ID propagation, and `X-Content-Type-Options: nosniff` from Phase 4.1 remain in effect.
- No credentials, task text, prompts, or raw provider errors were added to logs or responses.
- No new global mutable service state was added.

## Testing

Focused:

```text
.venv/bin/python -B -m pytest -p no:cacheprovider tests/unit/test_phase42_api.py -q
33 passed in 1.66s
```

Full offline suite:

```text
.venv/bin/python -B -m pytest -p no:cacheprovider -q
219 passed, 1 skipped in 19.42s
```

The skip is the existing optional PostgreSQL integration test. Focused coverage includes:

- Real deterministic execution through the existing agent runtime.
- Fake runner/reader injection and isolation between app instances.
- Completed, failed, and partially completed mapping.
- Missing/blank task, invalid mode, tool-call bounds, extra fields, malformed JSON, and invalid UUIDs.
- LLM mode without configuration and configured LLM delegation.
- Persistence failure sanitization and no-retry behavior.
- Runtime initialization failure mapping to `503 execution_unavailable`.
- Generic unexpected errors remaining sanitized `500` responses.
- Persisted summary mapping, redaction, unknown execution, and persistence-disabled behavior.
- Event pagination, ordering, next cursor, terminal page, redaction, query validation, and unknown execution.
- Repository pagination helper against a local SQLite schema.
- Phase 4.1 health/OpenAPI regression and Problem Details behavior.

Additional verification:

- Manual API smoke returned `201` for deterministic execution with `persisted=false`.
- `git diff --check` is clean.
- No lint/typecheck commands are currently declared in the project.

## Acceptance criteria

- Deterministic success, failure, and partial completion map correctly and return `201`.
- LLM mode without configuration returns `503 llm_not_configured`.
- Execution is persisted once through the existing runtime path.
- Persistence failures return `503` without triggering a retry.
- Retrieval distinguishes `404` and persistence-disabled `503`, and does not invent `final_output`.
- Event pages are ordered, bounded, and cursor-safe.
- Focused API tests require no PostgreSQL, network access, or external LLM.

## Deferred items and limitations

- No PostgreSQL/Docker integration run was performed; the SQLite test covers only the repository pagination helper.
- No migration was added; the existing schema and repository functions cover Phase 4.2.
- Global request-body-size limits remain deferred.
- Authentication, authorization, rate limiting, and public deployment hardening remain deferred; the API is local/single-tenant.
- Database readiness remains deferred; health is liveness-only.
- No async execution, cancellation, or durable job store exists.
- No execution list/search endpoint exists.
- Real external LLM provider calls were not tested.
- Provider/runtime failures are mapped where the underlying error is identifiable; arbitrary unexpected exceptions remain `500` by design.
- No lint/typecheck command is declared, so no separate static-analysis gate was run.

## Git state

All Phase 4.2 changes remain uncommitted and unstaged for review. No commit, push, or merge was performed. Phase 4.1 remains committed at `3271e28` and merged through PR #1.
