# syntax=docker/dockerfile:1.7
# LAZULA.ai images (INFRASTRUCTURE.md §3). The `studio` target (Chromium) is added in M6.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /usr/local/bin/uv
WORKDIR /app

FROM base AS deps
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM base AS api
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home /app app
COPY --from=deps /app/.venv /app/.venv
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH" \
    MASTER_KEY_FILE=/run/secrets/master_key
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["gunicorn", "app.main:app_factory()", "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "120", \
     "--graceful-timeout", "30", "--access-logfile", "-"]
