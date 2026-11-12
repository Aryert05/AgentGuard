FROM python:3.12-slim-bookworm AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY agentguard ./agentguard
RUN python -m pip wheel --wheel-dir=/wheels '.[api]'

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 10001 agentguard \
    && useradd --uid 10001 --gid agentguard --create-home --shell /usr/sbin/nologin agentguard

WORKDIR /app
COPY --from=build /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels 'agentguard[api]' \
    && rm -rf /wheels
COPY --chown=agentguard:agentguard alembic ./alembic
COPY --chown=agentguard:agentguard alembic.ini ./alembic.ini
COPY --chown=agentguard:agentguard scripts/grant_runtime_privileges.py ./scripts/grant_runtime_privileges.py

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "agentguard.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
