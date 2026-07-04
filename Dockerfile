# DataTool control plane image (ARCHITECTURE.md §18, §21.11).
#
# Builds the `datatool` CLI/daemon with uv. The runtime needs no compiler:
# psycopg ships as a binary wheel and numpy/scipy as manylinux wheels, so a slim
# Python base is enough. See docker-compose.yml for the Postgres + daemon stack.
FROM python:3.12-slim

# uv provides fast, reproducible installs straight from uv.lock.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first as a cached layer (changes only when the lock does),
# then install the project itself. README.md is referenced by pyproject metadata.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Application source, the layered contract config, and the example experiments.
COPY datatool ./datatool
COPY config ./config
COPY examples ./examples
RUN uv sync --frozen --no-dev

# Put the project venv on PATH so `datatool` is the bare entry point.
ENV PATH="/app/.venv/bin:$PATH" \
    DATATOOL_DATABASE_URL=postgresql+psycopg://datatool:datatool@postgres:5432/datatool

EXPOSE 8080

# `init` creates the schema (idempotent), then the daemon runs the control loop
# and serves the HTTP API + dashboard + /metrics on 8080.
CMD ["sh", "-c", "datatool init && datatool daemon --port 8080"]
