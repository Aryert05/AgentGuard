# Phase 6 — Deployment and Finalization Report

## Deployment delivered

- Added a multi-stage Python API image and a multi-stage Vite frontend image served by unprivileged Nginx.
- Expanded Compose to run PostgreSQL, a one-shot migration/privilege-grant job, the API, and the frontend. The database uses a named volume; published database and web ports bind to loopback by default.
- Separated bootstrap, migration, and application database roles. The application role has only the table privileges needed for telemetry operations and no schema, database, or role administration privileges.
- Production API settings disable interactive API documentation and default to no cross-origin access. Exact HTTP(S) origins can be configured explicitly. The frontend proxy forwards request IDs and serves the API under the same origin.
- Alembic logging configuration preserves existing application loggers when migrations run in-process, so migration setup does not disable API error logging.
- Replaced committed example credentials with empty `.env.example` password fields. Compose requires the operator to provide URL-safe development passwords in a local `.env` file.
- Rewrote the README with architecture, local setup, Docker deployment, migrations, API links, limitations, and marked screenshot placeholders.

## Verification performed

Verified on branch `phase/06-deployment`, based on `fd1932fd28cc707b2cef3eb7514ed21aff9b9901` (Phase 5 merge commit). The local disposable environment used Docker Engine 29.4.1 and PostgreSQL 16.15, with its own temporary Compose project, database, and volume.

- Frontend production image built successfully; the static dashboard was served through Nginx over loopback.
- PostgreSQL initialized, migration job completed successfully, and the API and web health checks became healthy.
- Alembic reported `0002_recovery_telemetry` as head.
- API smoke checks passed through the frontend proxy: request-ID round-trip, deterministic execution, `persisted=true`, history, execution summary, bounded two-page event retrieval, diagnosis of a completed execution, and diagnosis plus informational recommendation for a failed execution.
- Production API `/docs` returned 404.
- Database integration tests: `2 passed`. They exercised migration, restricted-role privileges, persistence and retrieval, and API persistence. The application role was confirmed non-superuser, without CREATEDB/CREATEROLE, database CREATE/TEMP, or schema CREATE; it had schema USAGE and the intended table grants. The integration test's denied administrative operations passed.
- Focused Phase 6 tests: `7 passed`.
- Failing Phase 4.1 logging test: `1 passed` independently; Phase 4.1 module: `33 passed`.
- Full test suite, with both PostgreSQL integration tests enabled and ordinary runtime persistence kept offline: `250 passed, 0 failed, 0 skipped`.

The full-suite failure was caused by Alembic's default `fileConfig()` behavior disabling previously created loggers during in-process migrations. `alembic/env.py` now calls `fileConfig(..., disable_existing_loggers=False)`, preserving the application logger. The full suite passed after this focused fix.

## Limitations

- Browser automation was unavailable in this environment, so no interactive browser rendering, JavaScript console, or browser network-panel inspection was performed. HTTP serving and same-origin API proxy behavior were checked directly.
- The image build reported a Vite bundle-size warning for the main JavaScript chunk (~663 kB minified). The frontend still built and served successfully.
- Authentication, TLS termination, public ingress, and rate limiting remain outside this local single-tenant deployment. Do not expose it publicly without those controls.
- This deployment does not implement Guarded Recovery.

## Cleanup and Git state

The temporary Phase 6 Compose project and its temporary database volume were removed. The pre-existing AgentGuard PostgreSQL container and named volume were left intact. No Phase 6 files were staged, committed, pushed, or submitted as a PR. The pre-existing untracked `scripts/verify_runtime.mjs` was preserved unchanged.
