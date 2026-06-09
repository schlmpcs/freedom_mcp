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

# ---- codex CLI: only used by the agent's AGENT_BACKEND=codex -----------------
# Static musl build from the official release, picked for the build's target
# arch (BuildKit sets TARGETARCH). Auth is NOT baked in — the server's
# `codex login` directory (~/.codex) is mounted into the agent container at
# runtime (see docker-compose.yml / deploy/DOCKER.md). Pin CODEX_VERSION to
# upgrade deterministically.
FROM debian:bookworm-slim AS codex
ARG CODEX_VERSION=0.138.0
ARG TARGETARCH
RUN set -eux; \
    apt-get update && apt-get install -y --no-install-recommends curl ca-certificates; \
    case "${TARGETARCH}" in \
      amd64) triple=x86_64-unknown-linux-musl ;; \
      arm64) triple=aarch64-unknown-linux-musl ;; \
      *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fL --retry 3 --proto '=https' --tlsv1.2 -o /tmp/codex.tar.gz \
      "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-${triple}.tar.gz"; \
    tar -xzf /tmp/codex.tar.gz -C /tmp; \
    install -m 0755 "/tmp/codex-${triple}" /usr/local/bin/codex; \
    /usr/local/bin/codex --version

# ---- runtime -----------------------------------------------------------------
FROM python:3.12-slim-bookworm

# tzdata: the bot schedules pushes in named zones (America/New_York, Asia/Karachi).
# ca-certificates: outbound HTTPS to the broker / Telegram / Anthropic.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user; /data holds persistent state (bot_state.json, agent.db).
# /data is mode 1777 (sticky, world-writable like /tmp) so the agent service can
# run as the *host* uid that owns the mounted ~/.codex login while bot/mcp run as
# `app` — both write to /data without an ownership clash.
RUN useradd --create-home --uid 10001 --shell /bin/bash app \
    && install -d -m 1777 /data

# Codex CLI for AGENT_BACKEND=codex (harmless dead weight for the mcp/bot services).
COPY --from=codex /usr/local/bin/codex /usr/local/bin/codex

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
