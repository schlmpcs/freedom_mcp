# syntax=docker/dockerfile:1.7
#
# Freedom24 MCP server + Telegram bot + paper-trading agent.
# One image, three entrypoints (selected by `command:` in docker-compose.yml):
#   - python freedom24_mcp.py      -> MCP server (stdio or streamable-http)
#   - python -m freedom24_bot      -> Telegram bot + automation worker
#   - python -m agent              -> autonomous paper-trading agent
#
# Build and runtime stages share the SAME python:3.12-slim base so the venv that
# `uv` builds in the builder (with its hard-coded interpreter path) stays valid
# when copied into the runtime stage. Do not change one base without the other.
#
# Secrets are NEVER baked in: .env is .dockerignore'd and injected at runtime via
# `env_file:` in compose (or `--env-file` with `docker run`).

# ---- builder: resolve deps into /app/.venv from the locked manifest ----------
FROM python:3.12-slim-bookworm AS builder

# Pull the uv binary (pin a tag for reproducibility if you prefer, e.g. :0.5.11).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Only the manifest + lock are needed to build the venv (the repo is a flat
# module layout with `package = false`, so no project build is required). Mounting
# them keeps this layer cache-stable until dependencies actually change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project \
        --extra http --extra bot --extra agent

# ---- runtime -----------------------------------------------------------------
FROM python:3.12-slim-bookworm

# tzdata: the bot schedules pushes in named zones (America/New_York, Asia/Karachi).
# ca-certificates: outbound HTTPS to the broker / Telegram / Anthropic.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user; /data holds persistent state (bot_state.json, agent.db).
RUN useradd --create-home --uid 10001 --shell /bin/bash app \
    && install -d -o app -g app /data

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Pre-built venv first, then source (source copy excludes .venv via .dockerignore,
# so it never clobbers the one we just copied).
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app . /app

USER app

# streamable-http transport listens here (override with MCP_PORT if you change it).
EXPOSE 8000

# Default entrypoint = the MCP server. Transport is chosen by MCP_TRANSPORT
# (stdio by default; set streamable-http for a long-lived networked server).
CMD ["python", "freedom24_mcp.py"]
