# Freedom24 / Tradernet MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets Claude Code interact
with your [Freedom24](https://freedom24.com) brokerage account through the
Tradernet API: portfolio, quotes, candles, orders, alerts, and reports.

> ⚠️ **This connects to a live brokerage account and can place real orders.**
> Order tools are guarded — `place_order` and `cancel_order` do nothing unless
> you pass `confirm=true`. You can also set `FREEDOM24_DRY_RUN=true` to block all
> order submission while testing.

## Features

| Area | Tools |
|------|-------|
| Auth | `login`, `login_api_key`, `get_session_info` |
| Portfolio | `get_portfolio`, `get_cashflows` |
| Quotes | `get_quote`, `get_candles`, `search_ticker`, `get_ticker_info`, `get_news`, `get_top_securities` |
| Orders | `get_active_orders`, `get_orders_history`, `place_order`, `cancel_order` |
| Market | `get_market_status`, `get_alerts`, `add_alert`, `delete_alert` |
| Reports | `get_broker_report`, `get_trades_history` |

## 1. Get your API keys

API keys are the recommended way to authenticate.

1. Log in to the Freedom24 web platform.
2. Go to **Settings → API**.
3. Create a key pair. You get a **public key** and a **secret (private) key**.
4. Copy both — the secret key is shown only once.

Requests are signed with **HMAC-SHA256** using the secret key (this server
handles that for you). Login/password auth is also supported, but some accounts
only expose API-key access.

## 2. Install

Requires Python 3.10+.

```bash
# from the project directory
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Or with [uv](https://github.com/astral-sh/uv): `uv pip install -r requirements.txt`.

## 3. Configure credentials

Copy the template and fill it in:

```bash
cp .env.example .env
```

```dotenv
# Option A — API key (recommended)
FREEDOM24_PUB_KEY=your_public_key
FREEDOM24_PRIV_KEY=your_secret_key

# Option B — login / password
FREEDOM24_LOGIN=your_login
FREEDOM24_PASSWORD=your_password

# Optional
FREEDOM24_API_URL=https://freedom24.com/api
FREEDOM24_TIMEOUT=15
FREEDOM24_DRY_RUN=false
```

`.env` is gitignored. The server auto-logs-in on the first tool call using
whichever credentials are present (API key takes priority).

Test it standalone — it should print startup info to stderr and then wait on
stdio:

```bash
python freedom24_mcp.py
```

## 4. Add to Claude Code

Add an entry to your MCP config (`~/.claude.json`, or via
`claude mcp add`). Use the **absolute path** to `freedom24_mcp.py`.

```json
{
  "mcpServers": {
    "freedom24": {
      "command": "python",
      "args": ["/absolute/path/to/freedom24_mcp.py"],
      "env": {
        "FREEDOM24_PUB_KEY": "your_public_key",
        "FREEDOM24_PRIV_KEY": "your_secret_key"
      }
    }
  }
}
```

Login/password variant:

```json
{
  "mcpServers": {
    "freedom24": {
      "command": "python",
      "args": ["/absolute/path/to/freedom24_mcp.py"],
      "env": {
        "FREEDOM24_LOGIN": "your_login",
        "FREEDOM24_PASSWORD": "your_password"
      }
    }
  }
}
```

> Tip: if you used a `.venv`, point `command` at that interpreter, e.g.
> `"command": "/absolute/path/to/.venv/bin/python"` (Windows:
> `".venv\\Scripts\\python.exe"`). Credentials in `env` here override `.env`.

Restart Claude Code (or run `/mcp`) and you should see the `freedom24` server
with its tools.

## 5. Example prompts

- "Check my Freedom24 session is working."
- "Show my Freedom24 portfolio and total P&L."
- "What's the current quote for AAPL.US?"
- "Pull the last 200 daily candles for TSLA.US and describe the trend."
- "Search for the Nvidia ticker."
- "What are my open orders?"
- "Place a limit order to buy 10 AAPL.US at 180 — but show me the details first."
  (Claude previews it; only after you approve does it re-call with `confirm=true`.)
- "Cancel order 123456." (again, requires confirmation)
- "Set a price alert for SBER.RU above 300."
- "Give me a broker report for 2026-01-01 to 2026-03-31."

## Command names

All API command (`cmd`) names live in one place — the `COMMANDS` dict in
`client.py`. Core commands (quotes, candles, security info, place/cancel order,
orders history, broker report) are grounded in public Tradernet/Freedom24
clients. A few are marked `# verify` (alerts, top-securities, news,
market-status, cashflows, ticker search, active orders) because they can differ
by API version or account.

If a tool returns an error like *"unknown command"* or a signature/param
complaint, check that command in `COMMANDS` against your account's API
documentation and adjust the one value. The request shape and signing stay the
same.

## How auth/signing works

- **API key (V2):** payload `{cmd, params, nonce, apiKey}` is flattened to a
  sorted bracket-notation query string (`apiKey=...&cmd=...&nonce=...&params[ticker]=AAPL`),
  HMAC-SHA256-signed with your secret key, and sent to `{api_url}/v2/cmd/{cmd}`
  as `x-www-form-urlencoded` with the hex digest in the `X-NtApi-Sig` header.
- **Login/password:** posts `{cmd, params}` to `{api_url}` to obtain a `sid`,
  then includes `sid` in every subsequent request. The session auto-refreshes
  (re-login + one retry) if it expires.
- **RSA-SHA256 (`auth.py: rsa_sign_nonce`)** is provided for the optional EDS
  "open security session" flow some accounts require to authorize writes. Set
  `FREEDOM24_RSA_PRIVATE_KEY` if you need it.

If signed requests are rejected with a signature error, the serialization in
`auth.py: convert_to_query_string` is the first thing to verify.

## Notes & limitations

- **REST only** for now. The Tradernet real-time feed is Socket.IO/WebSocket
  based; streaming doesn't map cleanly to request/response MCP tools, so it's
  left as a future addition (`websockets` is already in requirements).
- All calls have a 15s timeout (configurable via `FREEDOM24_TIMEOUT`).
- Every API call is logged to **stderr** (secrets redacted); stdout is reserved
  for the MCP protocol.
- Tools return the broker's raw JSON so Claude can analyze it naturally.

## Files

```
freedom_mcp/
├── freedom24_mcp.py   # MCP server + tool definitions
├── auth.py            # nonce, HMAC-SHA256 signing, RSA EDS helper
├── client.py          # HTTP client, command map, session handling
├── config.py          # .env / environment config loader
├── .env.example       # credential template
├── requirements.txt
└── README.md
```
