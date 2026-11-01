# Phase 3.4 — Failure Diagnostics & Bounded Recovery

**Branch:** `phase/03-ml-intelligence`
**Status:** Phase 3.4 remediation changes are implemented; independent re-audit remains pending. This is not an approval. PostgreSQL integration was not exercised. No Phase 3.3 models were retrained and no held-out test partition was scored.

## Architecture and service contracts

- `FailureDiagnosticEngine.diagnose(events, ml_signal=None)` consumes only the supplied event prefix and returns an immutable `FailureDiagnosis` with a primary category, secondary categories, confidence, summary, execution identity/status, patterns, and event-backed evidence.
- `FailurePatternDetector.detect(events)` is deterministic and validates a single execution/task, unique event IDs, chronological order, start-event placement, no events after terminal status, and terminal event/status consistency. An empty sequence is a valid pre-start snapshot; every non-empty prefix begins with one `execution_started` event. Failure terminal events use event status `failed`; `execution_status` metadata preserves `partially_completed` where applicable.
- `RecoveryRecommender.recommend(diagnosis)` maps the diagnosis to separately represented actions, rationale/safety basis, expected benefit, risk, approval requirement, max attempts, target tool, and evidence IDs. Alternative tool mappings are explicit allow-lists.
- `BoundedRecoveryController.recover(diagnosis, recommendations, executor, ...)` calls a supplied `RecoveryExecutor` one action at a time and records a `RecoverySession`. It does not invoke `run_agent`, the graph, an LLM, or another recovery chain.
- `DiagnosticService.analyze(...)` and `DiagnosticRecoveryService.analyze_and_recover(...)` are Python service boundaries for later API integration. `ModelRiskScorer` adapts an in-memory Phase 3.3 fitted bundle when one is available; absent or failing model scoring leaves rule diagnosis active.
- `DatabaseRecoveryTelemetry(session_factory)` connects the controller's telemetry sink to the existing `persist_recovery_session` transaction. The added Alembic migration creates separate recovery tables; it does not alter existing execution/event rows.

Callers must pass an event prefix representing the observation time. These functions cannot tell whether a caller accidentally passes a full trace when asking about an earlier point in time.

## Categories and detection rules

The engine supports `tool_failure`, `timeout`, `invalid_arguments`, `tool_unavailable`, `repeated_failure`, `repeated_action`, `excessive_usage`, `execution_limit_reached`, `incomplete_execution`, `possible_loop`, and `unknown`. A recorded tool failure, failed terminal event, or explicit tool-call-limit signal establishes `is_failure=True`. Repeated selection/loop/excessive-use patterns without a recorded failure are reported as risk signals and do not assert confirmed failure. ML probability is clearly marked as predictive evidence and never changes the rule-based category or confirmation flag.

Default configurable patterns are:

- Same known tool failing at least 3 times; same explicit error type at least 3 times.
- The same known tool reporting invalid arguments at least 3 times.
- At least 3 consecutive failures and trailing failed tool outcomes without progress.
- Alternation between two known failing tools across the last 4 tool failures.
- Same known tool selected at least 3 times; same tool and canonical-argument fingerprint selected at least 3 times.
- Two complete repetitions of a tool cycle of length 2 or 3.
- At least 6 selected tools; at least 2 timeout failures; at least 2 consecutive timeout outcomes.
- Explicit tool-call-limit evidence in event error fields.

No raw tool arguments are added to telemetry. Selection events include a canonical-JSON SHA-256 argument fingerprint for repeated-call detection. Such hashes support matching but are not a secrecy guarantee for low-entropy values. Rules that require an unknown tool or error identity do not group missing values under an invented `unknown` identity.

## Recommendations and recovery safety

- Timeout/unavailability may receive one retry when the affected tool is in the configured retry-safe set, with default capped backoff of 0.1, 0.25, and 0.5 seconds for successive attempts. If unavailability has a configured, approved alternative, that alternative is a separate bounded follow-up recommendation.
- The controller only repeats a retry after a `TimeoutError` or `ConnectionError`. Permanent and unclassified executor errors stop the recovery sequence immediately. Generic tool failures are escalated because their permanence and idempotency are unknown.
- Before execution, all recommendation evidence IDs must be present in the diagnosis evidence. The selected triggering event ID is carried unchanged into the executor request and attempt telemetry. If diagnosis evidence has no event ID, automatic recovery is blocked.
- Invalid arguments recommend a human-reviewed correction, but the controller does not execute argument modification. Incomplete resumes, human review, abort, and high-risk actions also remain non-automatic. This phase does not implement a human-approval workflow.
- Repeated failures/actions prefer an explicitly configured alternative when available; otherwise they are escalated. Loops, excessive usage, execution limits, and unknown evidence do not trigger blind retries.
- Default global recovery limit is 2; policy hard maximum is 5 total per execution and 3 per recommendation. The default in-memory ledger is local to a controller. Inject the same `InMemoryRecoveryAttemptLedger` into multiple controllers to coordinate within one process. `DatabaseRecoveryTelemetry` is the intended durable cross-process/restart-safe ledger; inject it as both telemetry and attempt ledger. Database concurrency has not been runtime-verified. Each request carries `chain_depth=1` and `tool_call_budget=1`. The controller rejects nested and concurrent reuse of the same instance, checks diagnosed/original execution ID consistency, and does not alter the original agent's own execution limit or state.
- The optional ML scorer output is validated inside the scorer fallback boundary. Missing, malformed, non-finite, and out-of-range predictions leave deterministic diagnosis active; errors raised by diagnosis itself remain visible.
- Recovery attempt completion timestamps earlier than their start are rejected by the controller, attempt model, and persistence boundary.
- `duration_ms` is executor-measured action duration (excluding controller backoff) when supplied; if absent, it is controller-measured elapsed attempt time including backoff. Values must be finite, nonnegative, and fit within the attempt timestamp envelope, allowing one microsecond for rounding. Repository validation also binds each persisted trigger ID to the session diagnosis evidence before opening a transaction.
- Recovery attempts and sessions record IDs, triggering event, diagnosis category, recommended/actual action, attempt number, status, UTC timestamps, duration, output/error, original/recovery execution IDs, and final outcome (`completed`, `recovered`, `partially_completed`, `failed`, or `unknown`). Recovery persistence is atomic and sanitizes output, error text, and metadata through the existing privacy boundary.

## Telemetry changes

Phase 3.4 adds migration `0002_recovery_telemetry` with recovery session, recovery attempt, and per-execution budget tables; model columns and indexes mirror the migration. Attempts reference their recovery session with cascade deletion, enforce unique attempt number per session and unique recovery execution ID, and constrain chain depth to one. Existing telemetry tables are unchanged. The runtime role needs SELECT/INSERT/UPDATE on `recovery_sessions`, SELECT/INSERT on `recovery_attempts`, and SELECT/INSERT/UPDATE on `recovery_attempt_budgets`. Migration-head discovery reports `0002_recovery_telemetry (head)`. No PostgreSQL instance or data was accessed, so applying the migration and exercising restricted database privileges remain unverified.

## Verification

- Phase 3.4 diagnostics/recovery tests: **41 passed** (including regression tests for the audit findings and final remediation).
- Phase 3.3 evaluation/training/analysis regression tests: **20 passed**.
- Phase 3.2 data/splitting/preprocessing regression tests: **37 passed**.
- Phase 1 regression tests: **25 passed**.
- Phase 2 unit regression tests: **30 passed**.
- Full suite: **153 passed, 1 skipped**. `tests/integration/test_postgres_persistence.py` skipped because `AGENTGUARD_TEST_DATABASE_URL` and `AGENTGUARD_TEST_MIGRATION_DATABASE_URL` were unset. No PostgreSQL/Docker access occurred.
- Package import checks and in-memory byte-compilation of 69 Python files: passed.
- `alembic heads`: `0002_recovery_telemetry (head)`.
- Offline demo with database URLs explicitly blanked: passed, calculator returned 3750, status `completed`.
- Type checker: none configured in `pyproject.toml`.
- `git diff --check`: passed.

The full test run emitted a non-failing joblib warning because this macOS environment returned an empty physical-core count; joblib fell back to logical cores.

## Limitations

The controller is an interface around a caller-supplied executor; this phase does not automatically replay an agent plan or wire recovery into `run_agent`. A custom executor must honor the one-tool-call budget and must not create another recovery controller/chain. The default offline ledger is controller-local; callers sharing a process-wide budget must inject the same ledger instance. Production callers must inject the database-backed ledger and grant the documented narrow table permissions for restart-safe limits. Approval decisions need a future explicit human workflow. Argument fingerprints can be guessable for low-entropy values. Rule confidence is an evidence-strength score, not a statistically calibrated probability. Recovery table persistence, atomic budget claims, migration behavior, and least-privilege grants still need verification in a disposable PostgreSQL integration environment.
