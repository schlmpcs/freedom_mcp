#!/usr/bin/env bash
#
# One-command installer for the Freedom24 MCP server.
#
# It uses `uv` to manage the virtualenv, dependencies, and a matching Python
# version for you, then registers the server with Claude Code. After this you
# only need to fill in your credentials in .env.
#
#   ./install.sh            # register for your user (available in every project)
#   ./install.sh --project  # register only for this repo (./.mcp.json)
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCOPE="user"
[[ "${1:-}" == "--project" ]] && SCOPE="project"

cd "$REPO"

# 1. uv ----------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  echo "error: 'uv' is not installed. Install it, then re-run:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  echo "  (or: brew install uv)" >&2
  exit 1
fi

# 2. dependencies + venv (uv fetches Python >=3.10 if needed) -----------------
# --all-extras keeps the single shared venv complete (MCP + bot + agent), since
# this repo runs all three from the same .venv.
echo "==> Installing dependencies with uv (creates .venv) ..."
uv sync --all-extras

# 3. credentials -------------------------------------------------------------
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "==> Created .env from template — edit it and add your FREEDOM24_PUB_KEY / FREEDOM24_PRIV_KEY."
else
  echo "==> .env already exists — leaving it untouched."
fi

# 4. register with Claude Code -----------------------------------------------
if ! command -v claude >/dev/null 2>&1; then
  echo "==> Claude Code CLI not found. Register the server manually with:" >&2
  echo "    claude mcp add freedom24 --scope $SCOPE -- uv run --directory \"$REPO\" python freedom24_mcp.py" >&2
  exit 0
fi

echo "==> Registering 'freedom24' MCP server (scope: $SCOPE) ..."
claude mcp remove freedom24 --scope "$SCOPE" >/dev/null 2>&1 || true
claude mcp add freedom24 --scope "$SCOPE" -- uv run --directory "$REPO" python freedom24_mcp.py

cat <<EOF

Done. Next steps:
  1. Make sure your credentials are set in $REPO/.env
  2. Restart Claude Code (or run /mcp) and ask: "Check my Freedom24 session is working."
EOF
