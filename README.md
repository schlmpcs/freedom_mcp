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

## Phase 2 — Telegram bot + automation worker

A separate, **deterministic** service (`freedom24_bot/`, run as `python -m
freedom24_bot`) that relays Freedom24 alert fires to your Telegram, answers
read-only account queries via slash commands, and pushes two scheduled reports.
It talks to the broker directly through `freedom24_core` — **no LLM, no token
cost** — and uses long-polling, so it needs no inbound network exposure.

> Alerts are **armed in the Freedom24 app** (price thresholds and % moves). The
> bot is read-only: it polls `getAlertsList`, detects fires, and relays them. It
> never places or cancels orders.

### Commands (only your configured chat ID may use them)

| Command | Action |
|---------|--------|
| `/portfolio` | Positions, P&L, cash balances. |
| `/quote TICKER` | Current quote, e.g. `/quote AAPL.US`. |
| `/orders` | Active orders. |
| `/alerts` | Currently armed price alerts. |
| `/report` | Daily snapshot on demand. |
| `/status` | Service health / auth mode. |
| `/help` | List commands. |

### Scheduled pushes

- **Pre-market heads-up** — daily at `BOT_PREMARKET_TIME` in `BOT_PREMARKET_TZ`
  (default 08:30 `America/New_York`, ~1h before the US open): market status,
  overnight moves on your holdings, open orders.
- **Daily snapshot** — daily at `BOT_SNAPSHOT_TIME` in `BOT_SNAPSHOT_TZ`
  (default 08:00 `Asia/Karachi`): positions, P&L, cash; reflects the prior US
  close. Both skip weekends. DST is handled automatically via `zoneinfo`.

### Setup

1. Create a bot with **@BotFather** and copy the token.
2. Get your numeric chat ID (message **@userinfobot**).
3. Install the bot's dependency into your venv (it's already in
   `requirements.txt`):
   ```bash
   pip install -r requirements.txt
   ```
4. Add to `.env` (see `.env.example` for the full block):
   ```dotenv
   TELEGRAM_BOT_TOKEN=123456:your-botfather-token
   TELEGRAM_CHAT_ID=000000000
   ```
5. Run locally **from the repo root** (`python -m freedom24_bot` resolves the
   package relative to the working directory — running it from elsewhere gives
   `No module named freedom24_bot`):
   ```bash
   .venv\Scripts\python.exe -m freedom24_bot      # Windows
   .venv/bin/python -m freedom24_bot              # Linux
   ```
   It logs `freedom24-bot starting (long-polling)`; message your bot `/help` and
   `/portfolio` to confirm, then `Ctrl-C`. If it logs `TELEGRAM_BOT_TOKEN and
   TELEGRAM_CHAT_ID are required`, the `.env` vars aren't set.

### Deploy (same droplet as the MCP server)

The droplet checkout is at **`/opt/freedom24`**. The systemd unit
(`deploy/freedom24-bot.service`) is written for that path — if your checkout
lives elsewhere, edit the three `/opt/freedom24` lines in it (or pipe through
`sed` as below) before installing.

```bash
cd /opt/freedom24
git pull

# install the new dependency into the droplet venv
/opt/freedom24/.venv/bin/pip install -r /opt/freedom24/requirements.txt

# add TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (and any BOT_* overrides) to .env
nano /opt/freedom24/.env

# smoke test before installing the service (Ctrl-C after /help works)
/opt/freedom24/.venv/bin/python -m freedom24_bot

# install + start the service
sudo cp deploy/freedom24-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now freedom24-bot

# verify
systemctl status freedom24-bot --no-pager
journalctl -u freedom24-bot -n 50 --no-pager
```

> If your checkout is **not** at `/opt/freedom24`, rewrite the paths on copy:
> `sudo sed 's#/opt/freedom24#/your/path#g' deploy/freedom24-bot.service | sudo tee /etc/systemd/system/freedom24-bot.service`

The bot reuses the existing `FREEDOM24_PUB_KEY`/`FREEDOM24_PRIV_KEY` for broker
auth and runs alongside `freedom24-mcp`. Redeploy after changes with
`cd /opt/freedom24 && git pull && sudo systemctl restart freedom24-mcp freedom24-bot`.

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

- **API key (V2):** payload `{cmd, params, nonce, apiKey}` is sent to
  `{api_url}/v2/cmd/{cmd}` as `x-www-form-urlencoded` with an HMAC-SHA256 digest
  in the `X-NtApi-Sig` header. The **signature** and the **body** use two
  *different* serializations and **neither is URL-encoded** (matching the official
  Tradernet `PublicApiClient` V2):
  - signature canonical (`convert_to_query_string`) — sorted, nested dicts recursed
    in place, no brackets: `…&params=date_from=…&date_to=…`
  - body (`url_form_encoded`) — sorted, bracket notation: `…&params[date_from]=…`
  - empty `params` is omitted entirely. Reusing one bracketed/encoded string for
    both (the old approach) yields `"Invalid signature provided"`.
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
├── freedom24_mcp.py   # MCP server + tool definitions (entry point)
├── middleware.py      # bearer-token ASGI middleware (HTTP transport)
├── freedom24_core/    # shared broker library (used by the MCP server AND the bot)
│   ├── auth.py        #   nonce, HMAC-SHA256 signing, RSA EDS helper
│   ├── client.py      #   HTTP client, command map, session handling
│   ├── config.py      #   .env / environment config loader
│   └── logging_setup.py
├── freedom24_bot/     # Phase 2: Telegram bot + automation worker (python -m freedom24_bot)
│   ├── __main__.py    #   builds the PTB app, registers handlers + jobs
│   ├── commands.py    #   read-only slash-command handlers
│   ├── alerts.py      #   alert poll + fire detection
│   ├── reports.py     #   pre-market + daily snapshot reports
│   ├── formatting.py  #   pure payload→string formatters
│   ├── scheduling.py  #   tz-aware report scheduling
│   ├── security.py    #   single-chat-id access control
│   └── state.py       #   relayed-alert-ID persistence
├── deploy/            # systemd unit(s)
├── tests/             # pytest suite
├── .env.example       # credential template
├── requirements.txt
└── README.md
```
