# AgentGuard

AgentGuard is an agent execution reliability project. It runs bounded tool-using tasks, records structured telemetry, diagnoses observed failures, and presents executions and traces in a dashboard. Deterministic mode works offline; provider-backed mode uses LangGraph and a configured chat model.

The repository contains the completed work across six phases: agent foundations, telemetry and a synthetic AgentTrace dataset, leakage-aware ML experiments, diagnostics and bounded recovery primitives, a FastAPI API, and a React observability dashboard. The dashboard's recovery recommendations are informational. **Guarded Recovery is deferred and is not triggered by the API or dashboard.**

## Architecture and technology

| Layer | Technology | Responsibility |
|---|---|---|
| Agent runtime | Python 3.11+, LangGraph, Pydantic | Deterministic offline runs and bounded provider-backed tool execution |
| API | FastAPI, Uvicorn | Versioned execution, trace, history, overview, diagnosis, and recommendation endpoints |
| Persistence | PostgreSQL 16, SQLAlchemy, Alembic, psycopg 3 | Execution/event telemetry and recovery service telemetry schema |
| Dashboard | React 18, TypeScript, Vite, Tailwind CSS, Recharts | Execution overview, history, event traces, diagnosis, and recommendations |
| Deployment | Docker Compose, Nginx | API, migration job, database, and compiled frontend |
| ML experiments | scikit-learn (optional), pandas, NumPy | Grouped synthetic-data baselines; no model is required to run the API |

Execution events and API responses omit raw tool arguments/results and apply redaction to sensitive-looking text and metadata. The synthetic AgentTrace data is for pipeline experimentation, not a substitute for production observations or evidence of production model quality. See the [Phase 1 guide](docs/phase-01-agent-foundation.md), [Phase 2 guide](docs/phase-02-telemetry-dataset.md), [Phase 3 ML plan](docs/phase-03-ml-plan.md), and [Phase 4 API plan](docs/phase-04-api-integration-plan.md).

## Capabilities

- Execute deterministic offline tasks or bounded provider-backed agent runs.
- Persist executions and ordered events to PostgreSQL through an atomic repository boundary.
- Browse execution history, overview metrics, and bounded event pages.
- Diagnose stateless or persisted event traces and return informational recommendations.
- Explore leakage-aware ML experiments using execution-held-out splits; task-template-held-out results remain exploratory.
- Use the single-tenant dashboard to inspect executions, traces, failure evidence, and recommendations.

## Prerequisites

- Python 3.11 or later.
- Node.js 20 or later and npm for local frontend development (Docker builds the frontend otherwise).
- Docker Engine and Docker Compose v2 for the complete container deployment.
- PostgreSQL is optional for offline development and required for persisted history and traces.

## Local development

### 1. Install and run the API

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev,api]'
uvicorn agentguard.api.main:create_app --factory --host 127.0.0.1 --port 8000 --reload
```

Without a database URL, deterministic execution and stateless diagnosis remain available. Persistent history/trace routes report that persistence is not configured. Interactive API docs are available at `http://127.0.0.1:8000/docs` in development.

### 2. Run the dashboard

In a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` to the local API. The default frontend API base is `/api/v1`; no browser-visible secret is needed.

### 3. Optional local PostgreSQL

Copy the environment template, generate three distinct URL-safe passwords, and put them in `POSTGRES_PASSWORD`, `AGENTGUARD_MIGRATION_PASSWORD`, and `AGENTGUARD_APP_PASSWORD` in `.env`. For example, generate each value with `openssl rand -hex 32`; do not commit `.env`.

```bash
cp .env.example .env
docker compose up -d postgres
```

The local database listens on `127.0.0.1:5434` by default. For a host-run API, set `AGENTGUARD_MIGRATION_DATABASE_URL` and `AGENTGUARD_DATABASE_URL` to the migration and runtime roles at `127.0.0.1:${POSTGRES_HOST_PORT}`. Use the URL-safe password values from `.env`. Then run the migration with the migration role:

```bash
.venv/bin/alembic upgrade head
python scripts/grant_runtime_privileges.py
```

The bootstrap role is used only to initialize PostgreSQL and create the two application roles. Alembic uses `agentguard_migrator`; the API uses the restricted `agentguard_app` role. The grant script gives the app only the table operations used by the repositories. On an existing database volume, initialization scripts do not run again; review the existing roles before using it. Never use `docker compose down -v` to troubleshoot credentials or migrations.

## Docker Compose deployment

The Compose stack builds a multi-stage Python API image and a multi-stage React frontend image, then runs PostgreSQL, a one-shot migration/grant job, the API, and Nginx. The browser talks to Nginx at one origin; Nginx proxies `/api/` to FastAPI. The default HTTP port is `127.0.0.1:8081`, and PostgreSQL is loopback-only on `127.0.0.1:5434`.

```bash
cp .env.example .env
# Set distinct values for POSTGRES_PASSWORD, AGENTGUARD_MIGRATION_PASSWORD,
# and AGENTGUARD_APP_PASSWORD. `openssl rand -hex 32` creates URL-safe values.
docker compose config --quiet
docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8081/api/v1/health
```

Compose initializes separate bootstrap, migration, and runtime identities on a fresh volume. The migration service waits for PostgreSQL health, applies `alembic upgrade head`, and grants the runtime role the exact application table permissions. The API waits for that job to succeed, and the frontend waits for API health. A failed migration prevents API startup. API docs default off in the production Compose environment.

View logs and stop the services with:

```bash
docker compose logs -f postgres migrate api web
docker compose down
```

`docker compose down` retains the named PostgreSQL data volume. Back up that volume/database before upgrades and do not delete it to troubleshoot deployment issues. Use a new, explicitly selected Compose project and volume for disposable verification rather than reusing a personal or production database.

### Production boundary

This Compose deployment binds the web entry point and PostgreSQL to loopback by default. It does not provide user authentication, TLS termination, rate limiting, or a multi-tenant authorization layer. Do not publish it directly to the Internet. A public deployment must place it behind a trusted gateway that supplies authentication, TLS, rate limits, and an appropriate request/access-log policy. Store production credentials in a deployment secret manager, not a checked-in file. `AGENTGUARD_CORS_ORIGINS` accepts an explicit comma-separated list of exact HTTP(S) origins; production has no cross-origin allowance by default, and credentialed CORS is disabled.

## API reference

The API is versioned under `/api/v1`:

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/health` | Application liveness; deliberately does not claim database readiness |
| `POST` | `/executions` | Synchronous bounded agent execution |
| `GET` | `/executions/history` | Filtered, paginated persisted execution summaries |
| `GET` | `/executions/overview` | Persisted execution metrics |
| `GET` | `/executions/{id}` | Persisted execution summary |
| `GET` | `/executions/{id}/events` | Ordered bounded event pages |
| `POST` | `/diagnoses` | Stateless event-prefix diagnosis and recommendations |
| `POST` | `/executions/{id}/diagnoses` | Diagnosis of a persisted trace |

`POST /executions` is synchronous and non-idempotent; do not retry it automatically. Agent-level `failed` or `partially_completed` results still return `201`; inspect the response status. When persistence is disabled, executions return `persisted=false`, while history and trace routes return a stable `503`. Tasks are bounded to 8,000 characters, tool calls to 1–8, and event pages to 1–500 records. Public responses do not include raw tool arguments, results, or internal state.

Interactive OpenAPI docs are enabled by default only in `development`, `local`, and `test`. For local API work, use `http://127.0.0.1:8000/docs`. In Compose, the API environment is `production`, so `/docs` and `/openapi.json` remain disabled unless deliberately reconfigured for controlled use.

## Screenshots

Screenshots are not included yet. Replace these placeholders with reviewed captures before using them in a portfolio presentation:

- `[Screenshot placeholder: System Overview dashboard]`
- `[Screenshot placeholder: Execution History and Trace Explorer]`
- `[Screenshot placeholder: Failure Intelligence and Recommendations]`

## Tests and ML experiments

Run the offline suite with:

```bash
python -m pip install -e '.[dev,api]'
python -m pytest
```

PostgreSQL integration tests require a separate disposable database and both runtime and migration test URLs; never point them at a valuable database. The optional ML preparation/training tools require `python -m pip install -e '.[dev,ml]'`. Phase 3 evaluation uses grouped execution splits as the primary estimate and template-held-out splits only as exploratory analysis. Final test partitions are not scored in the documented baseline runs. Generated datasets and analysis output are ignored by Git.

See [Phase 5 implementation details](docs/reports/phase-05-dashboard-report.md) and the Phase 4 reports under [`docs/reports`](docs/reports/).
