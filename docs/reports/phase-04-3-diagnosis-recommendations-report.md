# Phase 4.3 — Diagnosis and Recommendations API

**Status:** Implemented; awaiting review. No files were staged, committed, or pushed.

## Takeover and preserved work

The frozen roadmap identifies Phase 4.3 as Diagnosis and Recommendations and Phase 4.4 as PostgreSQL integration. At takeover, Phase 4.4 was present as uncommitted work in `agentguard/agent/runtime.py`, `agentguard/api/main.py`, `agentguard/application/execution_service.py`, `agentguard/application/protocols.py`, the Phase 4.2 API tests, and the PostgreSQL integration tests, with README and Phase 4.4 report updates. All of that work remains in the worktree. The Phase 4 plan and Phase 4.4 report were not modified.

Phase 4.3 reuses the existing Phase 3.4 `DiagnosticService`, `FailureDiagnosis`, evidence, patterns, and `RecoveryRecommendation` behavior. It does not implement another diagnosis algorithm.

## Endpoints and contracts

### `POST /api/v1/diagnoses`

Accepts `{"events": [...]}` using the existing `ExecutionEvent` schema. Empty prefixes are allowed; non-empty prefixes follow Phase 3.4's start-event, ordering, terminal, and identity validation. The request is limited to 1,000 events and rejects extra fields, including any client-supplied `ml_signal`.

### `POST /api/v1/executions/{execution_id}/diagnoses`

Reads a bounded event page from the injected `TraceReader` and passes it through the same `DiagnosticService`. Event order is the repository's deterministic `event_index` order. Traces over 1,000 events return `413 diagnosis_trace_too_large`; unknown executions return `404 execution_not_found`; disabled or failed persistence reads return sanitized `503 persistence_unavailable`.

Both routes return API-specific Pydantic DTOs containing diagnosis, patterns, evidence, optional ML risk, recommendations, and nullable `ml_error`. Neither endpoint writes a diagnosis or recovery record.

## Architecture and safety

- Routes are thin and use FastAPI dependency injection through `AppServices`.
- Application orchestration in `agentguard/application/diagnosis_service.py` owns persisted-trace loading, event model validation, error mapping, and response mapping.
- The optional `MLRiskScorer` is injected server-side through `create_app(ml_scorer=...)`. No scorer is configured by default. The existing core service validates scorer output and falls back to rule-based diagnosis on scorer failure. The API replaces exception-bearing `ml_error` details with a generic message.
- Output text and pattern metadata pass through the existing privacy redaction helpers.
- Recommendations are informational only. The API does not call `BoundedRecoveryController`, `RecoveryExecutor`, tools, or any retry path. No recovery session is created or persisted.

## Examples

Stateless request:

```json
{
  "events": [
    {
      "event_id": "evt-1",
      "execution_id": "exec-1",
      "task_id": "task-1",
      "step_number": 0,
      "timestamp": "2026-11-02T10:00:00Z",
      "event_type": "execution_started",
      "component": "agent",
      "status": "running",
      "metadata": {}
    }
  ]
}
```

Response shape:

```json
{
  "diagnosis": {
    "is_failure": false,
    "primary_category": "unknown",
    "secondary_categories": [],
    "confidence": 0.0,
    "summary": "No failure evidence was observed in the supplied event prefix.",
    "execution_id": "exec-1",
    "task_id": "task-1",
    "tool_name": null,
    "terminal_observed": false,
    "execution_status": null,
    "partial_results_available": false,
    "patterns": [],
    "evidence": [],
    "ml_risk": null
  },
  "recommendations": [],
  "ml_error": null
}
```

Use `curl -X POST http://127.0.0.1:8000/api/v1/executions/<execution_id>/diagnoses` for persisted-trace diagnosis. Invalid event schemas use the standard `422 validation_error`; semantic prefix violations use `422 invalid_diagnosis_prefix`. Errors retain the existing Problem Details format and request ID propagation.

## Verification

- Focused: `.venv/bin/pytest -q tests/unit/test_phase43_api.py tests/unit/test_phase42_api.py tests/unit/test_phase41_api.py` — **84 passed**.
- Full offline suite: `.venv/bin/pytest -q -rs` — **237 passed, 2 skipped**, with one non-failing joblib physical-core detection warning.
- The two skips are the Phase 4.4 PostgreSQL repository integration test and the API PostgreSQL integration test. They require `AGENTGUARD_TEST_DATABASE_URL` and `AGENTGUARD_TEST_MIGRATION_DATABASE_URL`; both were unset. No PostgreSQL/Docker access occurred.
- Regression tests cover stateless valid, empty, streaming, and invalid prefixes; pattern and recommendation serialization; persisted lookup, 404, bounded retrieval and safe 503 behavior; server scorer success and fallback; rejection of client signals; redaction; and the recovery execution boundary.
- `git diff --check`, API/persistence imports, and byte-compilation passed.

## Limitations

PostgreSQL API integration remains unverified, including live migration execution, restricted-role behavior on the API path, and database concurrency. The optional scorer is only an injection seam; no model artifact is configured. Automatic recovery remains intentionally outside the diagnosis API.
