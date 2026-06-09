# Docker deployment

Containerized deployment of the Freedom24 stack. One image (`Dockerfile`) with
three entrypoints, orchestrated by `docker-compose.yml`:

| Service | Command | Exposes | State |
|---------|---------|---------|-------|
| `mcp`   | `python freedom24_mcp.py` (streamable-HTTP) | `127.0.0.1:8000` | stateless |
| `bot`   | `python -m freedom24_bot` | — (long-poll) | `/data/bot_state.json` |
| `agent` | `python -m agent` (opt-in, `--profile agent`) | — | `/data/agent.db` |

> ⚠️ **Secrets.** `.env` holds **live brokerage credentials**. It is
> `.dockerignore`'d (never copied into the image) and injected at runtime via
> `env_file`. Do not pass secrets with `--build-arg` or `ENV`. Keep `.env`
> gitignored.

---

## 1. Configure

```bash
cp .env.example .env          # then edit it
```

Required for the **MCP HTTP** service:

```dotenv
FREEDOM24_PUB_KEY=...          # or login/password
FREEDOM24_PRIV_KEY=...
MCP_BEARER_TOKEN=...           # generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
```

`MCP_TRANSPORT` / `MCP_HOST` / `MCP_PORT` are **overridden by compose** for the
`mcp` service (forced to `streamable-http` on `0.0.0.0:8000` inside the
container), so you don't need to touch them in `.env`. The server refuses to
start in HTTP mode without a `MCP_BEARER_TOKEN`.

For the **bot**: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.
For the **agent**: `ANTHROPIC_API_KEY` (if `AGENT_BACKEND=api`).

## 2. Run

```bash
# Core services (MCP server + Telegram bot):
docker compose up -d --build mcp bot

# Also run the paper-trading agent (opt-in profile):
docker compose --profile agent up -d agent

# Logs / status:
docker compose logs -f mcp
docker compose ps
```

Expect `Starting MCP server (streamable-http) on 0.0.0.0:8000` in the `mcp` logs.

## 3. Connect Claude Code (HTTP transport)

The `mcp` service binds to **localhost only**. Put it behind a TLS reverse proxy
(nginx + Let's Encrypt) before exposing it — it carries live brokerage access.

```bash
claude mcp add --transport http freedom24 https://your-host/mcp \
  --header "Authorization: Bearer <MCP_BEARER_TOKEN>"
```

For a quick local test against the published port:

```bash
claude mcp add --transport http freedom24 http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer <MCP_BEARER_TOKEN>"
```

---

## Alternative: stdio transport (Claude Code launches the container)

Instead of a long-lived HTTP server, Claude Code can spawn the image per session
over **stdio** (the default transport — no token, no open port):

```bash
docker build -t freedom24:local .

claude mcp add freedom24 -- \
  docker run -i --rm --env-file /absolute/path/to/.env freedom24:local
```

`-i` keeps stdin attached for the MCP stdio transport; `--rm` cleans up when the
session ends. Use an **absolute** path to `.env`.

---

## Operating notes

- **Persistence**: `bot_state.json` and `agent.db` live on the named volume
  `freedom24-data` (mounted at `/data`). They survive restarts and rebuilds.
  Remove with `docker compose down -v` (this **deletes** that state).
- **Updating code**: `git pull && docker compose up -d --build`. Rebuilds are
  fast — the dependency layer is cached and only re-resolves when
  `pyproject.toml` / `uv.lock` change.
- **Timezones**: `tzdata` is installed in the image, so `BOT_SNAPSHOT_TZ` /
  `BOT_PREMARKET_TZ` named zones resolve correctly.
- **Safety**: `FREEDOM24_DRY_RUN`, the `confirm=True` order gate, and the
  fail-closed Telegram pre-trade notification all work identically in the
  container — they are enforced in code, not by the deployment.
- **Non-root**: the container runs as uid `10001` (`app`).

This replaces the host `systemd` units in this directory
(`freedom24-mcp.service`, `freedom24-bot.service`) for Docker-based hosts; the
units remain for bare-metal `/opt/freedom24` checkouts.
