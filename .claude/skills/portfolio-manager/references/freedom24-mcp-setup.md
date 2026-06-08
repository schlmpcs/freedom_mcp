# Freedom24 MCP Server Setup Guide

This is the **project-local** Portfolio Manager skill for the `freedom_mcp`
repository. It replaces the global skill's Alpaca data source with **this
project's Freedom24 / Tradernet MCP server**. Everything downstream of data
fetching (allocation, diversification, risk, position, rebalancing) is
unchanged.

> ⚠️ This project holds **live brokerage credentials**. Never print, paste,
> commit, or echo real account data (positions, balances, order ids, personal
> info) or secrets (`PUB_KEY`, `PRIV_KEY`, login, password, `sid`). Use
> placeholders only (e.g. `AAPL.US`) when showing examples.

## What is the Freedom24 MCP Server?

`freedom24_mcp.py` is a FastMCP server in this repo that exposes the Freedom24
brokerage account (via the Tradernet API) to Claude Code as MCP tools. It runs
**locally over stdio** — Claude Code launches it as a subprocess for the
duration of the session; there is no background daemon. When Claude Code closes,
the server stops.

## Prerequisites

### 1. Freedom24 account + Tradernet API access

You need a Freedom24 brokerage account with Tradernet API access. Auth uses
**either** an API key pair **or** login/password:

- **API key (preferred, V2 HMAC):** `PUB_KEY` + `PRIV_KEY`
- **Login/password fallback:** `LOGIN` + `PASSWORD`

Auth mode is auto-selected: API key if `PUB_KEY` + `PRIV_KEY` are present,
otherwise login/password.

### 2. Configure credentials in `.env`

Credentials are read by `config.py` from `.env` (which is **gitignored — keep it
that way**). Use `.env.example` as the template. Placeholder names only:

```dotenv
# API key auth (preferred)
PUB_KEY=PUB
PRIV_KEY=SECRET

# or login/password auth
# LOGIN=your_login
# PASSWORD=your_password

# Safety: blocks all order submission when true
FREEDOM24_DRY_RUN=true
```

Never commit `.env` or paste real key values anywhere.

### 3. Connect the MCP server in Claude Code

The server is started with the project venv interpreter. On Windows/PowerShell:

```powershell
.venv\Scripts\python.exe freedom24_mcp.py
```

Confirm the MCP is connected with `/mcp` in Claude Code. After editing
`auth.py` / `client.py` / `freedom24_mcp.py`, reconnect (`/mcp`) or restart
Claude Code to pick up changes.

## The data adapter

Freedom24's payloads are NOT Alpaca-shaped. This skill bridges them with the
project's pure-Python adapter:

**`freedom24_core/skill_adapter.py`** (stdlib only, no network, no I/O)

| Function | Purpose |
|----------|---------|
| `positions_to_schema(portfolio_json)` | Freedom24 `get_portfolio` → Alpaca-style `{account, positions}` |
| `quote_to_schema(quote_json)` | Freedom24 `get_quote` → `{symbol, last, bid, ask, volume}` |
| `candles_to_ohlcv(candles_json)` | Freedom24 `get_candles` → list of OHLCV dicts |
| `normalize_ticker("AAPL.US")` | → `"AAPL"` (strip exchange suffix) |
| `to_freedom24_ticker("AAPL")` | → `"AAPL.US"` (add suffix for MCP calls) |

The downstream analysis expects the Alpaca-style shape, so always run the raw
MCP JSON through the adapter before analyzing.

## MCP tools used by this skill (read-only for analysis)

### `mcp__freedom24__get_portfolio`
Returns all open positions, cash balances, and P&L. Raw shape is nested under
`result.ps` with `pos` (positions) and `acc` (accounts/cash) lists. **Always
pass the raw JSON through `positions_to_schema()`** rather than reading the raw
fields by hand.

### `mcp__freedom24__get_quote`
Current quote for a ticker (last/bid/ask/volume). Normalize with
`quote_to_schema()`. Use the Freedom24 symbol form, e.g. `AAPL.US`.

### `mcp__freedom24__get_candles`
OHLCV history for a ticker (for technical context). Normalize with
`candles_to_ohlcv()`.

### `mcp__freedom24__get_broker_report`
Date-ranged broker report (`from_date`, `to_date` as `YYYY-MM-DD`, optional
`report_type`). This is the closest substitute for portfolio history — see the
SKILL.md "Performance Analysis" notes. Useful `report_type` blocks:
`account_at_start`, `account_at_end`, `trades`, `commissions`, `in_outs`,
`cash_flows`.

### `mcp__freedom24__get_cashflows`
Cash-flow ledger (deposits, withdrawals, fees, dividends). Each row has
`type_code`, `sum`, `currency`, `date`, `comment`. Use to total net deposits and
estimate dividend income. No date params — uses `limit`/`skip`/`without_refund`.

## What is DIFFERENT from Alpaca

| Alpaca tool | Freedom24 equivalent |
|-------------|----------------------|
| `mcp__alpaca__get_account_info` | `account` block from `positions_to_schema(get_portfolio())` |
| `mcp__alpaca__get_positions` | `positions` block from `positions_to_schema(get_portfolio())` |
| `mcp__alpaca__get_portfolio_history` | **No equivalent.** Use `get_broker_report` (date-ranged) + `get_cashflows`. Time-weighted return / equity curve is NOT available; be honest about this. |

## Troubleshooting

### "Freedom24 MCP not connected"
1. Confirm the MCP is listed/connected via `/mcp`.
2. Verify `.env` has valid `PUB_KEY`+`PRIV_KEY` (or `LOGIN`+`PASSWORD`).
3. Restart Claude Code to reinitialize the MCP subprocess.

### "Invalid signature provided"
This is the V2 HMAC canonicalization issue documented in the repo `CLAUDE.md`
("How auth works"). It is an `auth.py` concern, not a skill concern — fix there.

### `getUserInfo` returns "Command disabled"
A key-permission limitation, not a bug. This skill does not depend on
`getUserInfo`; portfolio/quote reads work independently.

### Empty / no positions
Either the account is genuinely empty, or the portfolio payload arrived in an
unexpected shape. `positions_to_schema()` is defensive and returns an empty
`{account, positions}` (surfacing any `error` key) rather than throwing.

## Security best practices

- `.env` stays gitignored. Never stage or commit it.
- Keep `FREEDOM24_DRY_RUN=true` unless you are deliberately trading.
- This skill is **decision-support only** — it never places orders. See SKILL.md.
- Do not save real API responses (positions/quotes for the live account) into
  committed files, fixtures, or test snapshots.
