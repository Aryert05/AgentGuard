# AgentGuard Phase 2 — Verification Report

## Final hardening pass — current verification addendum

**Verification date:** 2026-11-01<br>
**Branch:** `phase/02-telemetry-dataset`

The final role and privilege correction was verified against a fresh temporary PostgreSQL 16 instance on `127.0.0.1:5434`, with a `tmpfs` data directory and randomly generated temporary credentials that were neither printed nor persisted in the repository. The existing development database/container/volume was not used.

| Command | Result |
|---|---|
| `AGENTGUARD_TEST_DATABASE_URL=<temporary runtime URL> AGENTGUARD_TEST_MIGRATION_DATABASE_URL=<temporary migration URL> PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider` | **56 passed, 0 failed, 0 skipped** |
| `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_agent.py` | **25 passed** |
| `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_phase2_database.py tests/unit/test_phase2_processing.py tests/unit/test_phase2_dataset.py` | **30 passed** |
| `AGENTGUARD_TEST_DATABASE_URL=<temporary runtime URL> AGENTGUARD_TEST_MIGRATION_DATABASE_URL=<temporary migration URL> PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_postgres_persistence.py` | **1 passed** against the isolated PostgreSQL 16 instance |

The PostgreSQL integration test covered persistence/retrieval, cross-call retry, exact and conflicting duplicate IDs in one batch, conflicting content across calls, rollback, FK enforcement, nested metadata redaction after mutation, and runtime-role access restrictions. Test cleanup left zero rows in both `executions` and `execution_events` before the temporary instance was removed.

Alembic applied `0001_execution_telemetry` using the distinct migration role; `alembic current` reported that revision at head. The bootstrap role created the migration and runtime roles. The migration role owned the `public` schema and its migrated objects, but not the database; it could migrate and grant table access without superuser, database-creation, or role-creation attributes.

Catalog privilege checks for the temporary `phase2_app` role confirmed: CONNECT allowed; database CREATE and TEMPORARY denied; schema USAGE allowed and CREATE denied; execution SELECT/INSERT/UPDATE allowed and DELETE denied; event SELECT/INSERT allowed and UPDATE/DELETE denied. `rolsuper`, `rolcreatedb`, and `rolcreaterole` were all false. The integration test also attempted and confirmed denial of role creation, persistent table creation, temporary table creation, and dropping a migration-owned table. TEMPORARY was revoked from PUBLIC on this disposable database; CONNECT was explicitly granted to migration/runtime roles.

The original AgentGuard development container ID remained unchanged, continued publishing only on `127.0.0.1:5433`, and retained the same `agentguard_agentguard-postgres` volume. The temporary container was removed, its `tmpfs` storage disappeared with it, and a final Docker inspection confirmed it was absent. No existing development database roles, schema, or rows were changed.

The temporary container and `tmpfs` storage were removed; final inspection confirmed that container was absent. The original AgentGuard development container ID and `agentguard_agentguard-postgres` volume remained unchanged. No existing database roles, schema, or rows were changed.

`git diff --check` passed. No files were staged or committed, and nothing was pushed. No credentials were added to source or reports; all checked-in connection strings use placeholders.

---

## Historical pre-hardening verification snapshot

The sections below record the earlier Phase 2 verification before this hardening pass; they are retained as historical context and do not supersede the current addendum above.

**Verification date:** 2026-10-31<br>
**Branch:** `phase/02-telemetry-dataset`<br>
**Python:** 3.13.12 (`.venv`)

## PostgreSQL diagnosis and safe correction

The initialized Compose database already had the expected `agentguard` role and `agentguard` database. The role could log in, create databases, and was the database owner. The container configuration reported `POSTGRES_USER=agentguard` and `POSTGRES_DB=agentguard`; no `.env` file existed in the checkout.

The failing `localhost:5432` endpoint was reaching a **native PostgreSQL process on the Mac**, not the Docker container. `lsof` showed native `postgres` listeners on `127.0.0.1:5432` and `[::1]:5432`. SQLAlchemy connecting there returned `FATAL: role "agentguard" does not exist`. The Docker container's own socket showed the role/database, and a connection to the Mac's non-loopback interface reached PostgreSQL 16.15 with the expected role/database. This established an endpoint/port collision rather than a missing role in the Compose volume.

Compose now publishes host port `5433` by default, `.env.example` uses that port, and `POSTGRES_HOST_PORT` allows local override. The existing container was recreated once because Docker cannot change a published port on a running container. The named volume remained the same before and after: `agentguard_agentguard-postgres` mounted at `/var/lib/postgresql/data`. No volume was deleted and no existing database was dropped. An empty `agentguard_test` database was added for isolated integration tests.

## Dependency versions observed

Python 3.13.12; SQLAlchemy 2.1.6; psycopg 3.3.6; Alembic 1.20.1; pandas 2.3.3; NumPy 2.5.4; Matplotlib 3.11.2; pytest 8.4.2; LangGraph 1.2.19; Pydantic 2.14.2. PostgreSQL server: 16.15 (`postgres:16-alpine`).

## Database connectivity, migration, and schema

SQLAlchemy connected successfully over `localhost:5433` as role `agentguard` to database `agentguard`. The role had CONNECT and public-schema CREATE privileges. The container reported healthy and published `5433 -> 5432`.

Commands executed (credentials are intentionally omitted from this report):

```bash
DATABASE_URL=<redacted configured PostgreSQL URL> .venv/bin/alembic upgrade head
DATABASE_URL=<redacted configured PostgreSQL URL> .venv/bin/alembic current
```

Alembic reported `0001_execution_telemetry (head)`. Catalog queries verified `executions` and `execution_events`, composite foreign key `fk_events_execution_task`, unique constraints `uq_executions_execution_task` and `uq_execution_event_order`, and the expected task/time/type/tool indexes. A read-only post-test check confirmed the dedicated test database retained the Alembic revision and no test execution rows.

Alembic configuration now contains `path_separator = os`; the PostgreSQL test and migration commands produced no prior `prepend_sys_path` deprecation warning.

## Test results

Commands and exact results:

| Command | Result |
|---|---|
| `AGENTGUARD_TEST_DATABASE_URL=<redacted PostgreSQL 16 test URL> PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -v -p no:cacheprovider tests/integration/test_postgres_persistence.py` | **1 passed** against PostgreSQL 16.15 |
| `AGENTGUARD_TEST_DATABASE_URL=<redacted PostgreSQL 16 test URL> PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider` | **47 passed, 0 failed, 0 skipped** |
| `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_agent.py` | **25 passed** |
| `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_phase2_database.py tests/unit/test_phase2_processing.py tests/unit/test_phase2_dataset.py` | **21 passed** |

The PostgreSQL integration test exercised exact duplicate event retry, conflicting content for an existing event ID, conflicting event order, transactional rollback, foreign-key enforcement, ordered retrieval, and failed/partially completed execution persistence. It ran against the separately created `agentguard_test` database; no SQLite substitute was used. The final full-suite run had no skips and no warnings.

## Dataset verification

Generation command:

```bash
.venv/bin/python -m agentguard.dataset.generator --executions 100 --seed 2026 --output data/generated/agenttrace-v1
```

Observed manifest: 100 synthetic executions, 584 events, 484 prefix samples, seed 2026. Execution outcomes: 44 completed, 50 failed, and 6 partially completed. Execution failure categories: none 44, tool exception 20, timeout 16, invalid arguments 8, unavailable 6, partial tool error 6. Prefix labels: 176 eventual success and 308 eventual failure. The CSV has 27 columns: 18 model features and 9 identifiers/metadata/target columns. There were zero duplicate complete rows.

Missing numeric values are represented as null/blank: elapsed time 100, failure rate 300, average duration 300, maximum duration 300, and time since last failure 362. Other columns had no missing values. Two in-memory runs with seed 2026 had identical rows and trace JSONL; manifest equality holds after excluding its generation timestamp. Prefix tests exclude terminal events, and `model_feature_matrix` tests exclude IDs and targets. The redundant `unique_tools_used` feature was removed.

## EDA execution and output verification

Command:

```bash
.venv/bin/python analysis/phase_02_eda.py
```

The script wrote `analysis/output/agenttrace-v1/eda_summary.json` and seven PNGs: `execution_outcomes.png`, `failure_categories.png`, `tool_usage.png`, `event_frequency.png`, `execution_duration.png`, `prefix_length.png`, and `feature_failure_relationships.png`. All images were opened and checked with Pillow. Execution/event/sample counts, outcomes, prefix labels, and categories were cross-checked against the CSV and JSONL.

The report shows 100 executions, 584 events, and 484 prefixes; execution outcomes 44/50/6; prefix labels 308 failures and 176 successes. Prefix failure rate correlated 0.77245 with the eventual failure label in this controlled sample. This is descriptive of synthetic constructed scenarios, not production predictive evidence.

## Documentation and Git checks

README, Phase 2 guide, implementation report, and this verification report cover setup, port selection, existing-volume behavior, integration tests, privacy, and dataset use. `.env.example` uses a placeholder password and is not an actual credential. `.env` remains absent and ignored. Dataset and EDA outputs are generated under Git-ignored directories.

`git diff --check` passed. No files are staged. No commit, push, merge, or branch switch occurred.

## Remaining limitations

- The current development bootstrap role is a PostgreSQL superuser. This is the existing local Compose initialization role; production should use a separate least-privilege application role.
- The host port `5433` avoids the native local listener; if overridden, the URL's port must match `POSTGRES_HOST_PORT`.
- Regex redaction is not comprehensive PII/secret detection. Error text is persisted.
- Persistence is post-execution rather than streaming; real production concurrency, retention, backups, and model validity remain outside this verification.

## Verification verdict

The migration was applied online to PostgreSQL 16, the expected schema/revision were confirmed, all PostgreSQL integration assertions passed against the disposable test database, and the full suite passed with no skips. Phase 2's requested PostgreSQL integration verification is **complete for this local development setup**. Production hardening and real-data modeling are not claimed.
