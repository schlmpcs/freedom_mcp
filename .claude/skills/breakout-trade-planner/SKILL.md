---
name: breakout-trade-planner
description: Generate Minervini-style breakout trade plans from VCP screener output with worst-case risk calculation, portfolio heat management, and Freedom24 place_order proposals (entry stop_limit plus SEPARATE protective stop and take-profit, since Freedom24 has no native bracket/OCO). Every order is a confirm=False proposal the user runs manually. Use when user has VCP screener results and wants actionable trade plans with entry/stop/target levels and position sizing for a Freedom24 account.
---

# Breakout Trade Planner (Freedom24)

Project-local override of the global `breakout-trade-planner` skill. It targets
**this repo's Freedom24 `place_order` MCP tool** instead of Alpaca brackets.
Generate trade plans from VCP screener output following Mark Minervini's
breakout methodology: calculate position sizes using worst-case entry prices,
enforce portfolio risk limits, and output **Freedom24 `place_order` proposals**.

## Decision-support only (critical)

This skill **never executes orders**. Every order it emits is a PROPOSAL — a
`place_order` call spec carrying `confirm: false`. The user reviews each
proposal and runs `place_order` manually, flipping `confirm=true` only after
explicit approval. The planner never imports the MCP client and never touches
the network.

## No native bracket — three separate orders

Alpaca's API places a single *bracket* order (entry + take-profit + stop-loss as
one OCO request). **Freedom24's `place_order` has no native bracket / OCO.** A
breakout plan is therefore expressed as THREE SEPARATE `place_order` proposals:

1. **entry** — a buy `stop_limit` to get into the position.
2. **stop_loss** — a protective sell `stop`, placed AFTER the entry fills.
3. **take_profit** — a profit-target sell `limit`, placed AFTER the entry fills.

The stop and target are independent follow-on orders (`place_after:
"entry_fill"`). They must NOT be placed before the entry fills (a naked sell
would short the account).

## stop_limit single-price limitation

The Freedom24 `place_order` tool takes ONE `price` argument. For a `stop_limit`
order it uses that single value as BOTH the stop trigger and the limit price
(see `freedom24_mcp.place_order`). The Alpaca template carried two distinct
levels (`stop_price=signal_entry`, `limit_price=worst_entry`); we cannot express
both in one call.

So the entry proposal uses **`signal_entry` as the single `price`** and surfaces
**`worst_entry` as a `limit_hint`** (plus a note). If you want a hard worst-case
ceiling, place a plain `limit` order at `worst_entry` instead. (In `post_confirm`
mode the entry is a plain `limit` at `worst_entry`, so this limitation does not
apply there.)

## Freedom24 proposal schema

Each proposal is a `place_order` call spec:

```json
{
  "proposal_type": "entry" | "stop_loss" | "take_profit",
  "tool": "mcp__freedom24__place_order",
  "ticker": "AAPL.US",          // base symbol -> Freedom24 form via to_freedom24_ticker
  "action": "buy" | "sell",
  "quantity": 10,
  "order_type": "stop_limit" | "stop" | "limit",
  "price": 583.32,              // entry stop_limit price / stop trigger / limit
  "limit_hint": 595.40,         // entry only: worst_entry ceiling (one price arg can't carry both)
  "confirm": false,             // ALWAYS false — decision-support only
  "place_after": null | "entry_fill" | "monitor_confirmation",
  "execution_mode": "pre_place" | "post_confirm",
  "requires_monitor_confirmation": false | true,
  "note": "human-readable PROPOSAL ONLY ... run place_order manually ..."
}
```

The actionable order's `order_proposals` holds two bundles:

```json
{
  "order_proposals": {
    "pre_place":   {"broker": "freedom24", "has_native_bracket": false, "proposals": [entry, stop_loss, take_profit]},
    "post_confirm":{"broker": "freedom24", "has_native_bracket": false, "entry_condition": {...}, "proposals": [entry(limit), stop_loss, take_profit]}
  }
}
```

## When to Use

- User has VCP screener JSON output and wants Freedom24 trade plans
- User asks for breakout entry/stop/target calculation
- User wants `place_order` proposals for VCP breakout candidates
- User needs position sizing with portfolio heat management

## Prerequisites

- VCP screener JSON output with `schema_version: "1.0"`
- This repo's `freedom24_core.skill_adapter` on the path (the scripts add the
  repo root automatically) — used to convert `AAPL` -> `AAPL.US`
- No API keys required to PLAN (planning is offline). Executing a proposal later
  uses the Freedom24 MCP credentials, manually.

## Workflow

### Step 1: Generate Trade Plans

```bash
.venv/bin/python .claude/skills/breakout-trade-planner/scripts/plan_breakout_trades.py \
  --input reports/vcp_screener_YYYY-MM-DD.json \
  --account-size 100000 \
  --risk-pct 0.5 \
  --output-dir reports/
```

### Step 2: Review Output

Read the generated JSON and Markdown reports. Present:

1. **Actionable Orders** — Pre-breakout candidates with `place_order` proposals
2. **Revalidation** — Breakout-state candidates needing live confirmation
3. **Watchlist** — Developing VCP candidates to monitor
4. **Rejected/Deferred/Constrained** — Candidates filtered by Gate or limits

### Step 3: Explain Trade Plans

For each actionable order, explain:
- Entry levels (signal vs worst-case) and stop-loss placement
- The three separate proposals (entry stop_limit -> after fill: sell stop + sell limit)
- The stop_limit single-price note and the worst_entry `limit_hint`
- R-multiple targets and reward-risk ratio
- Two execution modes: pre_place (stop_limit entry) vs post_confirm (limit entry
  after 5-min confirmation)
- Portfolio risk contribution and cumulative heat
- That EVERY proposal is `confirm=False` — the user runs `place_order` manually

## Minervini Gate (Filtering Criteria)

Candidates must pass ALL conditions:

| Condition | Pre-breakout | Breakout |
|-----------|-------------|----------|
| valid_vcp | True | True |
| rating_band | good/strong/textbook | good/strong/textbook |
| risk_pct_worst | <= 8.0% | <= 8.0% |
| breakout_volume | — | True |
| distance_from_pivot | — | <= max_chase_pct |
| current_price | — | <= worst_entry |

## CLI Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| --account-size | (required) | Account equity in dollars |
| --risk-pct | 0.5 | Base risk % per trade |
| --max-position-pct | 10.0 | Max single position % |
| --max-sector-pct | 30.0 | Max sector exposure % |
| --max-portfolio-heat-pct | 6.0 | Max total open risk % |
| --target-r-multiple | 2.0 | Take-profit R-multiple |
| --stop-buffer-pct | 1.0 | Stop buffer below contraction low |
| --max-chase-pct | 2.0 | Max chase above pivot |
| --pivot-buffer-pct | 0.1 | Pivot buffer for buy-stop trigger |
| --current-exposure-json | None | Existing portfolio exposure |

## Output

- `breakout_trade_plan_YYYY-MM-DD_HHMMSS.json` — Structured plans with Freedom24 `place_order` proposals
- `breakout_trade_plan_YYYY-MM-DD_HHMMSS.md` — Human-readable report

## Testing

```bash
.venv/bin/python -m pytest \
  ".claude/skills/breakout-trade-planner/scripts/tests/" -q
```

## Resources

- `references/minervini_entry_rules.md` — Entry methodology and rules
