---
name: portfolio-manager
description: Comprehensive portfolio analysis for THIS project's Freedom24 / Tradernet brokerage account via its local MCP server. Fetches holdings, cash, and quotes through the Freedom24 MCP, normalizes them with freedom24_core.skill_adapter, then analyzes asset allocation, risk metrics, individual positions, diversification, and generates rebalancing recommendations. Decision-support only — it never places orders. Use when user requests portfolio review, position analysis, risk assessment, performance evaluation, or rebalancing suggestions for their Freedom24 account.
---

# Portfolio Manager (Freedom24)

> **Project-local override.** This skill lives in `.claude/skills/` and shadows
> the global `portfolio-manager` skill for the `freedom_mcp` project only. The
> only thing that changed from the global skill is the **data-source layer**:
> data comes from THIS project's Freedom24 / Tradernet MCP server (not Alpaca),
> normalized through `freedom24_core/skill_adapter.py`. All downstream analysis
> (allocation, diversification, risk, position evaluation, rebalancing) is
> identical.

> 🔴 **Live brokerage credentials live in this repo.** Never print, paste,
> commit, or echo real account data (positions, balances, order ids, personal
> info) or secrets (`PUB_KEY`, `PRIV_KEY`, login, password, `sid`). Use
> placeholders (e.g. `AAPL.US`) in any example. See the project `CLAUDE.md`.

> 🟢 **Decision-support ONLY.** This skill analyzes and recommends. It MUST NOT
> place, modify, or cancel orders. When it suggests rebalancing trades, it emits
> them as **proposed** `mcp__freedom24__place_order(..., confirm=False)` calls
> for the user to review and run manually. NEVER set `confirm=True` here.

## Overview

Analyze and manage the Freedom24 investment portfolio by integrating with this
project's Freedom24 MCP server to fetch current holdings and cash, then
performing comprehensive analysis covering asset allocation, diversification,
risk metrics, individual position evaluation, and rebalancing recommendations.
Generate detailed portfolio reports with actionable insights.

The Freedom24 MCP runs locally over stdio (launched by Claude Code for the
session). Its payloads are Tradernet-shaped, so this skill routes them through
`freedom24_core.skill_adapter` to produce the Alpaca-style dicts the analysis
frameworks expect.

## When to Use

Invoke this skill when the user requests:
- "Analyze my portfolio"
- "Review my current positions"
- "What's my asset allocation?"
- "Check my portfolio risk"
- "Should I rebalance my portfolio?"
- "Evaluate my holdings"
- "Portfolio performance review"
- "What stocks should I buy or sell?"
- Any request involving portfolio-level analysis or management

## Prerequisites

### Freedom24 MCP Server (this project) + adapter

This skill requires **this project's Freedom24 MCP server** to be connected (it
replaces the Alpaca setup requirement of the global skill). It provides:
- Current portfolio positions and cash balances (`get_portfolio`)
- Real-time quotes for held securities (`get_quote`)
- OHLCV candle history for technical context (`get_candles`)
- Date-ranged broker reports and a cash-flow ledger (`get_broker_report`,
  `get_cashflows`) — used in place of a portfolio-history endpoint

**MCP server tools used (all read-only for analysis):**
- `mcp__freedom24__get_portfolio` — positions + cash (raw shape nested under `result.ps`)
- `mcp__freedom24__get_quote` — current quote for a ticker
- `mcp__freedom24__get_candles` — OHLCV history for a ticker
- `mcp__freedom24__get_broker_report` — date-ranged report (history substitute)
- `mcp__freedom24__get_cashflows` — deposits / withdrawals / fees / dividends ledger

**The adapter** that normalizes Freedom24 payloads into the Alpaca-style shape
the analysis expects lives at **`freedom24_core/skill_adapter.py`**
(`positions_to_schema`, `quote_to_schema`, `candles_to_ohlcv`,
`normalize_ticker`, `to_freedom24_ticker`). It is pure Python (stdlib only, no
network).

If the Freedom24 MCP is not connected, inform the user and provide setup
instructions from `references/freedom24-mcp-setup.md`.

## Workflow

### Step 1: Fetch Portfolio Data via the Freedom24 MCP (then normalize)

Freedom24's portfolio payload is Tradernet-shaped (positions under
`result.ps.pos`, cash under `result.ps.acc`). Do **not** read those raw fields by
hand — fetch the raw JSON from the MCP, then run it through
`freedom24_core.skill_adapter.positions_to_schema()` to get the Alpaca-style
`{account, positions}` shape the downstream analysis already expects.

**1.1 Get positions + account in one call and normalize:**

Call the MCP tool, then normalize the returned JSON with the adapter. A short
Python snippet the model runs (e.g. via the project venv interpreter):

```python
import json
from freedom24_core.skill_adapter import positions_to_schema

# `raw` is the JSON string returned by mcp__freedom24__get_portfolio
data = positions_to_schema(json.loads(raw))

account   = data["account"]    # {"equity", "cash", "buying_power", "currency"}
positions = data["positions"]  # list of normalized position dicts
# data may also carry an "error" key if the MCP call failed — handle it.
```

Equivalently, in prose: call `mcp__freedom24__get_portfolio`, parse its JSON,
then pass it to `positions_to_schema()` and read `account` + `positions` from the
result.

**Field mapping (adapter output → analysis):**

From `account`:
- `equity` → total portfolio value (sum of position market values + USD cash)
- `cash` → settled USD cash balance
- `buying_power` → reported as USD cash (Freedom24 portfolio exposes no margin
  figure, so the adapter conservatively uses settled cash)
- `currency` → account currency (USD-centric; non-USD cash is excluded from
  equity to avoid mixing currencies without an FX rate)

From each entry in `positions`:
- `symbol` → ticker, suffix-stripped (e.g. `AAPL`; `broker_symbol` keeps `AAPL.US`)
- `qty` → quantity held
- `avg_entry_price` → cost basis per share (derived: `(market_value − unrealized_pl) / qty`)
- `current_price` → current market price
- `market_value` → current position value
- `unrealized_pl` and `unrealized_pl_pct` → unrealized P&L ($ and %)
- `weight_pct` → position size as % of equity

**Data Validation:**
- Verify all positions have valid ticker symbols (use `broker_symbol` for MCP
  follow-up calls like `get_quote`; `to_freedom24_ticker()` can re-add a suffix
  if you only have the base symbol)
- Confirm market values plus cash sum to `equity` (the adapter computes equity
  this way, so they will reconcile by construction)
- If the adapter returns an `error` key, surface it and stop (the MCP call failed
  or auth is misconfigured — see `references/freedom24-mcp-setup.md`)
- Handle edge cases (fractional shares, options tickers like
  `+AAPL.26MAY2026.P287.5`, non-USD positions)

### Step 2: Enrich Position Data

For each position in the portfolio, gather additional market data and fundamentals:

**2.1 Current Market Data:**
- Real-time or delayed price quotes — fetch via `mcp__freedom24__get_quote`
  using the position's `broker_symbol` (e.g. `AAPL.US`), then normalize with
  `quote_to_schema()` → `{symbol, last, bid, ask, volume}`
- Price history / trend context — fetch via `mcp__freedom24__get_candles`, then
  normalize with `candles_to_ohlcv()` → list of OHLCV dicts (oldest → newest)
- Daily volume and liquidity metrics
- 52-week range
- Market capitalization

**2.2 Fundamental Data:**
Use WebSearch or available market data APIs to fetch:
- Sector and industry classification
- Key valuation metrics (P/E, P/B, dividend yield)
- Recent earnings and financial health indicators
- Analyst ratings and price targets
- Recent news and material developments

**2.3 Technical Analysis:**
- Price trend (20-day, 50-day, 200-day moving averages) — compute from
  `candles_to_ohlcv()` output
- Relative strength
- Support and resistance levels
- Momentum indicators (RSI, MACD if available)

### Step 3: Portfolio-Level Analysis

Perform comprehensive portfolio analysis using frameworks from reference files:

#### 3.1 Asset Allocation Analysis

**Read references/asset-allocation.md** for allocation frameworks

Analyze current allocation across multiple dimensions:

**By Asset Class:**
- Equities vs Fixed Income vs Cash vs Alternatives
- Compare to target allocation for user's risk profile
- Assess if allocation matches investment goals

**By Sector:**
- Technology, Healthcare, Financials, Consumer, etc.
- Identify sector concentration risks
- Compare to benchmark sector weights (e.g., S&P 500)

**By Market Cap:**
- Large-cap vs Mid-cap vs Small-cap distribution
- Concentration in mega-caps
- Market cap diversification score

**By Geography:**
- US vs International vs Emerging Markets
- Domestic concentration risk assessment

**Output Format:**
```markdown
## Asset Allocation

### Current Allocation vs Target
| Asset Class | Current | Target | Variance |
|-------------|---------|--------|----------|
| US Equities | XX.X% | YY.Y% | +/- Z.Z% |
| ... |

### Sector Breakdown
[Pie chart description or table with sector percentages]

### Top 10 Holdings
| Rank | Symbol | % of Portfolio | Sector |
|------|--------|----------------|--------|
| 1 | AAPL | X.X% | Technology |
| ... |
```

#### 3.2 Diversification Analysis

**Read references/diversification-principles.md** for diversification theory

Evaluate portfolio diversification quality:

**Position Concentration:**
- Identify top holdings and their aggregate weight
- Flag if any single position exceeds 10-15% of portfolio
- Calculate Herfindahl-Hirschman Index (HHI) for concentration measurement

**Sector Concentration:**
- Identify dominant sectors
- Flag if any sector exceeds 30-40% of portfolio
- Compare to benchmark sector diversity

**Correlation Analysis:**
- Estimate correlation between major positions
- Identify highly correlated holdings (potential redundancy)
- Assess true diversification benefit

**Number of Positions:**
- Optimal range: 15-30 stocks for individual portfolios
- Flag if under-diversified (<10 stocks) or over-diversified (>50 stocks)

**Output:**
```markdown
## Diversification Assessment

**Concentration Risk:** [Low / Medium / High]
- Top 5 holdings represent XX% of portfolio
- Largest single position: [SYMBOL] at XX%

**Sector Diversification:** [Excellent / Good / Fair / Poor]
- Dominant sector: [Sector Name] at XX%
- [Assessment of balance across sectors]

**Position Count:** [Optimal / Under-diversified / Over-diversified]
- Total positions: XX stocks
- [Recommendation]

**Correlation Concerns:**
- [List any highly correlated position pairs]
- [Diversification improvement suggestions]
```

#### 3.3 Risk Analysis

**Read references/portfolio-risk-metrics.md** for risk measurement frameworks

Calculate and interpret key risk metrics:

**Volatility Measures:**
- Estimated portfolio beta (weighted average of position betas)
- Individual position volatilities
- Portfolio standard deviation (if historical data available)

**Downside Risk:**
- Maximum drawdown (estimate from candle history / broker report — Freedom24 has
  no portfolio-history equity curve; see Step 3.4)
- Current drawdown from peak
- Positions with significant unrealized losses

**Risk Concentration:**
- Percentage in high-volatility stocks (beta > 1.5)
- Percentage in speculative/unprofitable companies
- Leverage usage (if applicable)

**Tail Risk:**
- Exposure to potential black swan events
- Single-stock concentration risk
- Sector-specific event risk

**Output:**
```markdown
## Risk Assessment

**Overall Risk Profile:** [Conservative / Moderate / Aggressive]

**Portfolio Beta:** X.XX (vs market at 1.00)
- Interpretation: Portfolio is [more/less] volatile than market

**Maximum Drawdown:** -XX.X% (from $XXX,XXX to $XXX,XXX)
- Current drawdown from peak: -XX.X%

**High-Risk Positions:**
| Symbol | % of Portfolio | Beta | Risk Factor |
|--------|----------------|------|-------------|
| [TICKER] | XX% | X.XX | [High volatility / Recent loss / etc] |

**Risk Concentrations:**
- XX% in single sector ([Sector])
- XX% in stocks with beta > 1.5
- [Other concentration risks]

**Risk Score:** XX/100 ([Low/Medium/High] risk)
```

#### 3.4 Performance Analysis

Evaluate portfolio performance using available data.

> ⚠️ **No portfolio-history endpoint on Freedom24.** Unlike Alpaca, the
> Freedom24 / Tradernet API exposes **no `get_portfolio_history`** equivalent —
> there is no ready-made equity/P&L time series, so **true time-weighted returns
> and an equity curve are NOT available via this broker.** Be honest about this;
> do not fabricate a return series. Substitute the following:
>
> - **`mcp__freedom24__get_broker_report(from_date, to_date)`** — date-ranged
>   report. Use `report_type="account_at_start"` and `"account_at_end"` to get
>   period-boundary balances (a coarse start-vs-end change), `"trades"` for
>   realized activity, and `"commissions"` for fees over the period.
> - **`mcp__freedom24__get_cashflows`** — the cash-flow ledger (deposits,
>   withdrawals, fees, dividends). Use it to total **net deposits/withdrawals**
>   so a simple-return estimate is not distorted by external cash flows, and to
>   estimate dividend income.
>
> A rough period return can be approximated as
> `(equity_end − equity_start − net_external_cashflows) / equity_start`, clearly
> labeled as an **estimate** with its assumptions. If even period boundaries are
> unavailable, state that performance-over-time is **not available via this
> broker** and rely on current unrealized P&L (below) instead.

**Absolute Returns (always available from current holdings):**
- Overall portfolio unrealized P&L ($ and %) — sum `unrealized_pl` across positions
- Best performing positions (top 5 by % gain, via `unrealized_pl_pct`)
- Worst performing positions (bottom 5 by % loss)

**Time-Weighted Returns (only if approximated from broker report — label as estimate):**
- Period return estimate (see boxed note above)
- Compare to benchmark (S&P 500, relevant index) only with the same caveat

**Position-Level Performance:**
- Winners vs Losers ratio
- Average gain on winning positions
- Average loss on losing positions
- Positions near 52-week highs/lows

**Output:**
```markdown
## Performance Review

**Total Portfolio Value:** $XXX,XXX
**Total Unrealized P&L:** $XX,XXX (+XX.X%)
**Cash Balance:** $XX,XXX (XX% of portfolio)

> Note: Freedom24 provides no portfolio-history series; period return below is an
> estimate from broker-report start/end balances net of cash flows, not a
> time-weighted return.

**Best Performers:**
| Symbol | Gain | Position Value |
|--------|------|----------------|
| [TICKER] | +XX.X% | $XX,XXX |
| ... |

**Worst Performers:**
| Symbol | Loss | Position Value |
|--------|------|----------------|
| [TICKER] | -XX.X% | $XX,XXX |
| ... |

**Performance vs Benchmark (estimate only, if computable):**
- Portfolio return (est): +X.X%
- S&P 500 return: +Y.Y%
- Alpha (est): +/- Z.Z%
```

### Step 4: Individual Position Analysis

For key positions (top 10-15 by portfolio weight), perform detailed analysis:

**Read references/position-evaluation.md** for position analysis framework

For each significant position:

**4.1 Current Thesis Validation:**
- Why was this position initiated? (if known from user context)
- Has the investment thesis played out or broken?
- Recent company developments and news

**4.2 Valuation Assessment:**
- Current valuation metrics (P/E, P/B, etc.)
- Compare to historical valuation range
- Compare to sector peers
- Overvalued / Fair / Undervalued assessment

**4.3 Technical Health:**
- Price trend (uptrend, downtrend, sideways)
- Position relative to moving averages
- Support and resistance levels
- Momentum status

**4.4 Position Sizing:**
- Current weight in portfolio
- Is size appropriate given conviction and risk?
- Overweight or underweight vs optimal

**4.5 Action Recommendation:**
- **HOLD** - Position is well-sized and thesis intact
- **ADD** - Underweight given opportunity, thesis strengthening
- **TRIM** - Overweight or valuation stretched
- **SELL** - Thesis broken, better opportunities elsewhere

**Output per position:**
```markdown
### [SYMBOL] - [Company Name] (XX.X% of portfolio)

**Position Details:**
- Shares: XXX
- Avg Cost: $XX.XX
- Current Price: $XX.XX
- Market Value: $XX,XXX
- Unrealized P/L: $X,XXX (+XX.X%)

**Fundamental Snapshot:**
- Sector: [Sector]
- Market Cap: $XX.XB
- P/E: XX.X | Dividend Yield: X.X%
- Recent developments: [Key news or earnings]

**Technical Status:**
- Trend: [Uptrend / Downtrend / Sideways]
- Price vs 50-day MA: [Above/Below by XX%]
- Support: $XX.XX | Resistance: $XX.XX

**Position Assessment:**
- **Thesis Status:** [Intact / Weakening / Broken / Strengthening]
- **Valuation:** [Undervalued / Fair / Overvalued]
- **Position Sizing:** [Optimal / Overweight / Underweight]

**Recommendation:** [HOLD / ADD / TRIM / SELL]
**Rationale:** [1-2 sentence explanation]
```

### Step 5: Rebalancing Recommendations

**Read references/rebalancing-strategies.md** for rebalancing approaches

Generate specific rebalancing recommendations.

> 🟢 **Decision-support only.** Output recommendations and **proposed** orders
> for the user to run manually. Do NOT call `place_order` with `confirm=True`.
> See "Proposed Orders" below.

**5.1 Identify Rebalancing Triggers:**
- Positions that have drifted significantly from target weights
- Sector/asset class allocations requiring adjustment
- Overweight positions to trim (exceeded threshold)
- Underweight areas to add (below threshold)
- Tax considerations (capital gains implications)

**5.2 Develop Rebalancing Plan:**

**Positions to TRIM:**
- Overweight positions (>threshold deviation from target)
- Stocks that have run up significantly (valuation concerns)
- Concentrated positions exceeding 15-20% of portfolio
- Positions with broken thesis

**Positions to ADD:**
- Underweight sectors or asset classes
- High-conviction positions currently underweight
- New opportunities to improve diversification

**Cash Deployment:**
- If excess cash (>10% of portfolio), suggest deployment
- Prioritize based on opportunity and allocation gaps

**5.3 Prioritization:**
Rank rebalancing actions by priority:
1. **Immediate** - Risk reduction (trim concentrated positions)
2. **High Priority** - Major allocation drift (>10% from target)
3. **Medium Priority** - Moderate drift (5-10% from target)
4. **Low Priority** - Fine-tuning and opportunistic adjustments

**Output:**
```markdown
## Rebalancing Recommendations

### Summary
- **Rebalancing Needed:** [Yes / No / Optional]
- **Primary Reason:** [Concentration risk / Sector drift / Cash deployment / etc]
- **Estimated Trades:** X sell orders, Y buy orders

### Recommended Actions

#### HIGH PRIORITY: Risk Reduction
**TRIM [SYMBOL]** from XX% to YY% of portfolio
- **Shares to Sell:** XX shares (~$XX,XXX)
- **Rationale:** [Overweight / Valuation extended / etc]
- **Tax Impact:** $X,XXX capital gain (est)

#### MEDIUM PRIORITY: Asset Allocation
**ADD [Sector/Asset Class]** exposure
- **Target:** Increase from XX% to YY%
- **Suggested Stocks:** [SYMBOL1, SYMBOL2, SYMBOL3]
- **Amount to Invest:** ~$XX,XXX

#### CASH DEPLOYMENT
**Current Cash:** $XX,XXX (XX% of portfolio)
- **Recommendation:** [Deploy / Keep for opportunities / Reduce to X%]
- **Suggested Allocation:** [Distribution across sectors/stocks]

### Proposed Orders (for the user to review and run manually)

> These are NOT executed by this skill. Each is a proposed MCP call with the
> safety gate left OFF (`confirm=False`). Review, then run manually and set
> `confirm=True` yourself only when you intend to trade. Use the Freedom24
> symbol form (e.g. AAPL.US).

```text
mcp__freedom24__place_order(ticker="AAPL.US", action="sell", quantity=XX, price=XXX.XX, order_type="limit", confirm=False)
mcp__freedom24__place_order(ticker="MSFT.US", action="buy",  quantity=YY, price=YYY.YY, order_type="limit", confirm=False)
```

### Implementation Plan
1. [First action - highest priority]
2. [Second action]
3. [Third action]
...

**Timing Considerations:**
- [Tax year-end planning / Earnings season / Market conditions]
- [Suggested phasing if applicable]
```

### Step 6: Generate Portfolio Report

Create comprehensive markdown report saved to repository root:

**Filename:** `portfolio_analysis_YYYY-MM-DD.md`

> ⚠️ This is a live account: the report may contain real balances and positions.
> Save it locally for the user but do **not** commit or stage it (it is account
> data). If unsure, ask before writing it to disk.

**Report Structure:**

```markdown
# Portfolio Analysis Report

**Account:** [Account type if available]
**Report Date:** YYYY-MM-DD
**Portfolio Value:** $XXX,XXX
**Total P&L:** $XX,XXX (+XX.X%)

---

## Executive Summary

[3-5 bullet points summarizing key findings]
- Overall portfolio health assessment
- Major strengths
- Key risks or concerns
- Primary recommendations

---

## Holdings Overview

[Summary table of all positions]

---

## Asset Allocation
[Section from Step 3.1]

---

## Diversification Analysis
[Section from Step 3.2]

---

## Risk Assessment
[Section from Step 3.3]

---

## Performance Review
[Section from Step 3.4 — note Freedom24 history limitation]

---

## Position Analysis
[Detailed analysis of top 10-15 positions from Step 4]

---

## Rebalancing Recommendations
[Section from Step 5 — proposed orders with confirm=False only]

---

## Action Items

**Immediate Actions:**
- [ ] [Action 1]
- [ ] [Action 2]

**Medium-Term Actions:**
- [ ] [Action 3]
- [ ] [Action 4]

**Monitoring Priorities:**
- [ ] [Watch list item 1]
- [ ] [Watch list item 2]

---

## Appendix: Full Holdings

[Complete table with all positions and metrics]
```

### Step 7: Interactive Follow-up

Be prepared to answer follow-up questions:

**Common Questions:**

**"Why should I sell [SYMBOL]?"**
- Explain specific concerns (valuation, thesis breakdown, concentration)
- Provide supporting data
- Offer alternative positions if applicable

**"What should I buy instead?"**
- Suggest specific stocks to improve allocation
- Explain how they address portfolio gaps
- Provide brief investment thesis

**"What's my biggest risk?"**
- Identify primary risk factor (concentration, sector exposure, volatility)
- Quantify the risk
- Suggest mitigation strategies

**"How does my portfolio compare to [benchmark]?"**
- Compare allocation, sector weights, risk metrics
- Highlight key differences
- Assess if differences are justified

**"Should I rebalance now or wait?"**
- Consider market conditions, tax implications, transaction costs
- Provide timing recommendation with rationale

**"Can you analyze [specific position] in more detail?"**
- Perform deep-dive analysis using us-stock-analysis skill if needed
- Integrate findings back into portfolio context

**"Can you place these trades for me?"**
- No. This skill is decision-support only. Provide the proposed
  `mcp__freedom24__place_order(..., confirm=False)` calls and let the user run
  them and set `confirm=True` themselves.

## Analysis Frameworks

### Target Allocation Templates

This skill includes reference allocation models for different investor profiles:

**Read references/target-allocations.md** for detailed models:

- **Conservative** (Capital preservation, income focus)
- **Moderate** (Balanced growth and income)
- **Growth** (Long-term capital appreciation)
- **Aggressive** (Maximum growth, high risk tolerance)

Each model includes:
- Asset class targets (Stocks/Bonds/Cash/Alternatives)
- Sector guidelines
- Market cap distribution
- Geographic allocation
- Position sizing rules

Use these as comparison benchmarks when user hasn't specified their allocation strategy.

### Risk Profile Assessment

If user's target allocation is unknown, assess appropriate risk profile based on:
- Age (if mentioned)
- Investment timeline (if mentioned)
- Current allocation (reveals preferences)
- Position types (conservative vs speculative stocks)

**Read references/risk-profile-questionnaire.md** for assessment framework

## Output Guidelines

**Tone and Style:**
- Objective and analytical
- Actionable recommendations with clear rationale
- Acknowledge uncertainty in market forecasts
- Balance optimism with risk awareness
- Quantify whenever possible

**Data Presentation:**
- Tables for comparisons and metrics
- Percentages for allocations and returns
- Dollar amounts for absolute values
- Consistent formatting throughout report

**Recommendation Clarity:**
- Explicit action verbs (TRIM, ADD, HOLD, SELL)
- Specific quantities (sell XX shares, add $X,XXX)
- Priority levels (Immediate, High, Medium, Low)
- Supporting rationale for each recommendation

**Visual Descriptions:**
- Describe allocation breakdowns as if creating pie charts
- Sector weights as bar chart equivalents
- Performance trends with directional indicators (↑ ↓ →)

## Reference Files

Load these references as needed during analysis:

**references/freedom24-mcp-setup.md**
- When: Freedom24 MCP is not connected, or you need the tool/adapter map
- Contains: this project's MCP startup (venv interpreter, stdio), `.env`
  credential setup (placeholder names only), the `freedom24_core/skill_adapter`
  function map, the Alpaca→Freedom24 tool mapping, and troubleshooting

**references/asset-allocation.md**
- When: Analyzing portfolio allocation or creating rebalancing plan
- Contains: Asset allocation theory, optimal allocation by risk profile, sector allocation guidelines, rebalancing triggers

**references/diversification-principles.md**
- When: Assessing portfolio diversification quality
- Contains: Modern portfolio theory basics, correlation concepts, optimal position count, concentration risk thresholds, diversification metrics

**references/portfolio-risk-metrics.md**
- When: Calculating risk scores or interpreting volatility
- Contains: Beta calculation, standard deviation, Sharpe ratio, maximum drawdown, Value at Risk (VaR), risk-adjusted return metrics

**references/position-evaluation.md**
- When: Analyzing individual holdings for buy/hold/sell decisions
- Contains: Position analysis framework, thesis validation checklist, position sizing guidelines, sell discipline criteria

**references/rebalancing-strategies.md**
- When: Developing rebalancing recommendations
- Contains: Rebalancing methodologies (calendar-based, threshold-based, tactical), tax optimization strategies, transaction cost considerations, implementation timing

**references/target-allocations.md**
- When: Need benchmark allocations for comparison
- Contains: Model portfolios for conservative/moderate/growth/aggressive investors, sector target ranges, market cap distributions

**references/risk-profile-questionnaire.md**
- When: User hasn't specified risk tolerance or target allocation
- Contains: Risk assessment questions, scoring methodology, risk profile classification

## Error Handling

**If the Freedom24 MCP is not connected:**
1. Inform user that this project's Freedom24 MCP integration is required
2. Provide setup instructions from `references/freedom24-mcp-setup.md`
3. Offer alternative: manual data entry (less ideal, user provides CSV of positions)

**If `positions_to_schema()` returns an `error` key:**
- The MCP call failed (auth, signature, or connectivity). Surface the error
  message, point to `references/freedom24-mcp-setup.md`, and stop — do not
  fabricate holdings.

**If API returns incomplete data:**
- Proceed with available data
- Note limitations in report
- Suggest manual verification for missing positions

**If position data seems stale:**
- Flag the issue
- Recommend reconnecting the MCP (`/mcp`) or restarting Claude Code
- Proceed with analysis but caveat findings

**If user has no positions:**
- Acknowledge empty portfolio (adapter returns empty `account`/`positions`)
- Offer portfolio construction guidance instead of analysis
- Suggest using value-dividend-screener or us-stock-analysis for stock ideas

## Advanced Features

### Tax-Loss Harvesting Opportunities

Identify positions with unrealized losses suitable for tax-loss harvesting:
- Positions with losses >5%
- Holding period considerations (avoid wash sale rule)
- Replacement security suggestions (similar but not substantially identical)

### Dividend Income Analysis

For portfolios with dividend-paying stocks:
- Estimate annual dividend income (cross-reference dividend entries in
  `mcp__freedom24__get_cashflows`)
- Dividend growth rate trajectory
- Dividend coverage and sustainability
- Yield on cost for long-term holdings

### Correlation Matrix

For portfolios with 5-20 positions:
- Estimate correlation between major positions
- Identify redundant positions (correlation >0.8)
- Suggest diversification improvements

### Scenario Analysis

Model portfolio behavior under different scenarios:
- **Bull Market** (+20% equity appreciation)
- **Bear Market** (-20% equity decline)
- **Sector Rotation** (Tech weakness, Value strength)
- **Rising Rates** (Impact on growth stocks and bonds)

## Example Queries

**Basic Portfolio Review:**
- "Analyze my portfolio"
- "Review my positions"
- "How's my portfolio doing?"

**Allocation Analysis:**
- "What's my asset allocation?"
- "Am I too concentrated in tech?"
- "Show me my sector breakdown"

**Risk Assessment:**
- "Is my portfolio too risky?"
- "What's my portfolio beta?"
- "What are my biggest risks?"

**Rebalancing:**
- "Should I rebalance?"
- "What should I buy or sell?"
- "How can I improve diversification?"

**Performance:**
- "What are my best and worst positions?"
- "How am I performing vs the market?"
- "Which stocks are winning and losing?"

**Position-Specific:**
- "Should I sell [SYMBOL]?"
- "Is [SYMBOL] overweight in my portfolio?"
- "What should I do with [SYMBOL]?"

## Limitations and Disclaimers

**Include in all reports:**

*This analysis is for informational purposes only and does not constitute
financial advice. Investment decisions should be made based on individual
circumstances, risk tolerance, and financial goals. Past performance does not
guarantee future results. Consult with a qualified financial advisor before
making investment decisions.*

*This skill is decision-support only: it analyzes and recommends but does NOT
place, modify, or cancel orders. Any suggested trades are proposed
`mcp__freedom24__place_order(..., confirm=False)` calls for you to review and
execute manually.*

*Data accuracy depends on the Freedom24 / Tradernet API and third-party market
data sources. Verify critical information independently. Freedom24 provides no
portfolio-history endpoint, so performance-over-time figures are estimates
derived from broker reports and cash flows, not time-weighted returns. Tax
implications are estimates only; consult a tax professional for specific
guidance.*
